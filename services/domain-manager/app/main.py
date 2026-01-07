import asyncio
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.rest import ApiException

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Keycluster Domain Manager")

NAMESPACE = "keycloak"
INGRESS_CLASS = "nginx"

class DomainMapping(BaseModel):
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
                "nginx.ingress.kubernetes.io/use-regex": "true"
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
async def sync_domain(mapping: DomainMapping):
    """Create or update a domain mapping for a realm."""
    api = await get_kubernetes_client()
    ingress_name = f"keycloak-realm-{mapping.realm}"
    
    if not mapping.enabled:
        return await delete_domain(mapping.realm)

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
            
    return {"status": "synced", "realm": mapping.realm, "domain": mapping.domain}

@app.delete("/domains/{realm}")
async def delete_domain(realm: str):
    """Delete a domain mapping for a realm."""
    api = await get_kubernetes_client()
    ingress_name = f"keycloak-realm-{realm}"
    
    try:
        await api.delete_namespaced_ingress(ingress_name, NAMESPACE)
        logger.info(f"Deleted ingress for realm {realm}")
    except ApiException as e:
        if e.status != 404:
            raise HTTPException(status_code=e.status, detail=str(e))
            
    return {"status": "deleted", "realm": realm}

@app.post("/cleanup")
async def cleanup_orphans(background_tasks: BackgroundTasks):
    """Trigger a background job to remove orphan ingresses."""
    background_tasks.add_task(run_cleanup)
    return {"status": "cleanup_triggered"}

async def run_cleanup():
    """Logic to remove ingresses managed by us that aren't in a 'desired' list."""
    # This would typically fetch the list of active realms from your main DB
    # For now, it's a stub where you'd integrate your source of truth.
    logger.info("Running orphan cleanup task...")
    pass

@app.get("/health")
async def health():
    return {"status": "ok"}
