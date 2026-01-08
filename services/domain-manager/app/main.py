import asyncio
import logging
from typing import Optional, List
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.rest import ApiException
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.domain import DomainMapping as DomainMappingModel

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Keycluster Domain Manager")

NAMESPACE = "keycloak"
INGRESS_CLASS = "nginx"

class DomainMappingSchema(BaseModel):
    realm: str
    domain: str
    enabled: bool = True

async def get_kubernetes_client():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    return client.NetworkingV1Api()

def generate_ingress_manifest(realm: str, domain: str, theme_name: str = "dynamic-standard"):
    """
    Hyper-secure isolation:
    1. Only allows access to the specific realm's endpoints.
    2. Strictly limits static resources to the 'common' assets and the OWN theme.
    3. Blocks access to any other theme or realm.
    """
    rules = [
        # Explicit Realm Access
        (f"/realms/{realm}", "Prefix"),
        (f"/admin/{realm}", "Prefix"),
        
        # Explicit Shared Assets (Keycloak Core)
        ("/resources/common", "Prefix"),
        ("/static", "Prefix"),
        ("/js", "Prefix"),
        
        # Explicit Theme Assets (Only this theme)
        (f"/resources/.*/login/{theme_name}", "ImplementationSpecific"),
        (f"/resources/.*/email/{theme_name}", "ImplementationSpecific"),
        
        # Basic site files
        ("/robots.txt", "Exact"),
        ("/favicon.ico", "Exact")
    ]
    
    ingress_paths = [
        {
            "path": p,
            "pathType": pt,
            "backend": {
                "service": {
                    "name": "keycloak",
                    "port": {"number": 8080}
                }
            }
        } for p, pt in rules
    ]

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": f"keycloak-realm-{realm}",
            "namespace": NAMESPACE,
            "annotations": {
                "nginx.ingress.kubernetes.io/proxy-buffer-size": "128k",
                "nginx.ingress.kubernetes.io/use-regex": "true",
                "nginx.ingress.kubernetes.io/ssl-redirect": "false" # Tunneled
            },
            "labels": {
                "app.kubernetes.io/managed-by": "keycluster-domain-manager",
                "keycluster.io/realm": realm
            }
        },
        "spec": {
            "ingressClassName": INGRESS_CLASS,
            "rules": [
                {
                    "host": domain,
                    "http": {
                        "paths": ingress_paths
                    }
                }
            ]
        }
    }

@app.post("/domains")
async def sync_domain(
    mapping: DomainMappingSchema, 
    session: AsyncSession = Depends(get_session)
):
    """Create or update a domain mapping for a realm."""
    api = await get_kubernetes_client()
    ingress_name = f"keycloak-realm-{mapping.realm}"
    
    # 1. Update Database
    statement = select(DomainMappingModel).where(DomainMappingModel.realm == mapping.realm)
    results = await session.execute(statement)
    db_mapping = results.scalar_one_or_none()

    if not mapping.enabled:
        if db_mapping:
            await session.delete(db_mapping)
            await session.commit()
        return await delete_domain_ingress(mapping.realm, api)

    if not db_mapping:
        db_mapping = DomainMappingModel(realm=mapping.realm, domain=mapping.domain, enabled=mapping.enabled)
        session.add(db_mapping)
    else:
        db_mapping.domain = mapping.domain
        db_mapping.enabled = mapping.enabled
    
    await session.commit()
    await session.refresh(db_mapping)

    # 2. Update Kubernetes
    manifest = generate_ingress_manifest(mapping.realm, mapping.domain)
    try:
        try:
            # Check if exists
            await api.read_namespaced_ingress(ingress_name, NAMESPACE)
            # Update
            await api.replace_namespaced_ingress(ingress_name, NAMESPACE, manifest)
            logger.info(f"Updated ingress for realm {mapping.realm}")
        except ApiException as e:
            if e.status == 404:
                # Create
                await api.create_namespaced_ingress(NAMESPACE, manifest)
                logger.info(f"Created ingress for realm {mapping.realm}")
            else:
                raise e
    except ApiException as e:
        logger.error(f"Kubernetes API Error: {e.status} - {e.body}")
        raise HTTPException(status_code=e.status, detail=f"K8s Error: {e.body}")
            
    return {"status": "synced", "realm": mapping.realm, "domain": mapping.domain, "db_id": db_mapping.id}

async def delete_domain_ingress(realm: str, api):
    ingress_name = f"keycloak-realm-{realm}"
    try:
        await api.delete_namespaced_ingress(ingress_name, NAMESPACE)
        logger.info(f"Deleted ingress for realm {realm}")
    except ApiException as e:
        if e.status != 404:
            raise HTTPException(status_code=e.status, detail=str(e))
    return {"status": "deleted", "realm": realm}

@app.delete("/domains/{realm}")
async def delete_domain(
    realm: str,
    session: AsyncSession = Depends(get_session)
):
    """Delete a domain mapping for a realm."""
    # 1. DB Delete
    statement = select(DomainMappingModel).where(DomainMappingModel.realm == realm)
    results = await session.execute(statement)
    db_mapping = results.scalar_one_or_none()
    
    if db_mapping:
        await session.delete(db_mapping)
        await session.commit()

    # 2. K8s Delete
    api = await get_kubernetes_client()
    return await delete_domain_ingress(realm, api)

@app.post("/cleanup")
async def cleanup_orphans(
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session)
):
    """Trigger a background job to remove orphan ingresses."""
    # Note: Passing session to background task can be tricky due to context scope.
    # Typically we'd create a new session in the task or read data here.
    # For simplicity, we'll read valid realms here and pass list to task.
    statement = select(DomainMappingModel.realm)
    results = await session.execute(statement)
    valid_realms = results.scalars().all()
    
    background_tasks.add_task(run_cleanup, valid_realms)
    return {"status": "cleanup_triggered", "valid_realms_count": len(valid_realms)}

async def run_cleanup(valid_realms: List[str]):
    """Logic to remove ingresses managed by us that aren't in the DB."""
    logger.info(f"Running orphan cleanup. Valid realms: {valid_realms}")
    api = await get_kubernetes_client()
    
    try:
        # List all ingresses managed by us
        label_selector = "app.kubernetes.io/managed-by=keycluster-domain-manager"
        ingresses = await api.list_namespaced_ingress(NAMESPACE, label_selector=label_selector)
        
        for ing in ingresses.items:
            realm_label = ing.metadata.labels.get("keycluster.io/realm")
            if realm_label and realm_label not in valid_realms:
                logger.warning(f"Found orphan ingress for realm {realm_label}. Deleting...")
                try:
                    await api.delete_namespaced_ingress(ing.metadata.name, NAMESPACE)
                    logger.info(f"Deleted orphan ingress: {ing.metadata.name}")
                except Exception as e:
                    logger.error(f"Failed to delete orphan {ing.metadata.name}: {e}")
                    
    except ApiException as e:
        logger.error(f"Failed to list ingresses for cleanup: {e}")

@app.get("/health")
async def health():
    return {"status": "ok"}
