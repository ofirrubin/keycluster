import requests
import sys

# Requirements: pip install requests
# Assumes port-forwarding: kubectl port-forward svc/domain-manager 8000:80 -n keycloak

BASE_URL = "http://localhost:8000"
DOMAINS_URL = f"{BASE_URL}/domains"

def test_domain_manager_health():
    print("Testing Domain Manager health...")
    try:
        response = requests.get(f"{BASE_URL}/health")
        if response.status_code == 200:
            print("✅ Domain Manager is alive.")
        else:
            print(f"❌ Domain Manager returned {response.status_code}")
    except Exception as e:
        print(f"❌ Failed to reach Domain Manager: {e}")

def register_domain(realm, domain):
    print(f"Registering domain {domain} for realm {realm}...")
    payload = {"realm": realm, "domain": domain}
    response = requests.post(DOMAINS_URL, json=payload)
    if response.status_code == 200:
        print(f"✅ Registered {domain}")
        return True
    else:
        print(f"❌ Failed to register: {response.text}")
        return False

def verify_isolation(domain, target_realm):
    # This requires /etc/hosts to be set up or simulating Host header
    print(f"Testing isolation on {domain} for realm {target_realm}...")
    
    # We use the Host header trick so we don't depend on actual DNS/hosts file
    # We hit the ingress-nginx IP if we can find it, otherwise we hit localhost
    # For minikube testing, we often use 'minikube ip'
    
    # Let's assume the user is testing via localhost if they have another port-forward 
    # OR we try to hit the Minikube IP directly.
    # A better way is to tell the user to use the Host header.
    
    print("Isolation check should be performed against the Ingress IP with Host headers.")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        register_domain("realm-a", "auth.realm-a.com")
        register_domain("realm-b", "auth.realm-b.com")
    else:
        test_domain_manager_health()
        print("\nRun 'python tests/system_test.py setup' to create test realms.")
