# Helper commands for keycluster

deploy-db:
	kubectl apply -f k8s/postgres.yaml

deploy-keycloak:
	# Note: Build the docker image first if using the custom theme
	kubectl apply -f k8s/keycloak.yaml

deploy-domain-manager:
	# Note: Build the docker image first
	kubectl apply -f k8s/domain-manager.yaml

build:
	eval $$(minikube docker-env) && \
	docker build -t keycloak-custom:latest . && \
	docker build -t domain-manager:latest ./services/domain-manager

restart:
	kubectl rollout restart deployment keycloak -n keycloak
	kubectl rollout restart deployment domain-manager -n keycloak

update: build restart

deploy-all: deploy-db deploy-keycloak deploy-domain-manager

logs:
	kubectl logs -l app=keycloak -n keycloak -f

status:
	kubectl get all -n keycloak

tunnels:
	./tests/test-tunnel.sh

test:
	./tests/integration_test.sh

test-full:
	./tests/run-test-full.sh

install-test-deps:
	pip install fastapi uvicorn pydantic requests

theme-lab:
	python3 tests/theme_mock_api.py

# Example: make theme-update COLOR="#ff5500"
theme-update:
	curl -X POST http://localhost:8001/v1/themes/example \
		-H "Content-Type: application/json" \
		-d '{"primaryColor": "$(COLOR)", "logoUrl": "https://upload.wikimedia.org/wikipedia/commons/a/af/Free_pdt_logo.png"}'

theme-switch:
	./tests/switch-theme.sh $(THEME)

# Test the new hyper-customization
theme-test-advanced:
	curl -X POST http://localhost:8001/v1/themes/example \
		-H "Content-Type: application/json" \
		-d '{\
			"primaryColor": "#10b981", \
			"showRealmName": false, \
			"footerText": "🚀 Powered by Keycluster. Accept <a href=\"#\">Terms</a>", \
			"customCss": "h1#kc-page-title { color: gold !important; font-size: 3rem !important; }", \
			"loginTitle": "Welcome to the Future", \
			"loginButtonText": "Secure Access 🔒", \
			"themeMode": "system" \
		}'

theme-auto:
	curl -X POST http://localhost:8001/v1/themes/example \
		-H "Content-Type: application/json" \
		-d '{"themeMode": "system"}'

theme-hebrew:
	curl -X POST http://localhost:8001/v1/themes/example \
		-H "Content-Type: application/json" \
		-d '{\
			"primaryColor": "#2563eb", \
			"themeMode": "light", \
			"translations": { \
				"he": { \
					"loginTitle": "ברוכים הבאים", \
					"loginButtonText": "כניסה מאובטחת", \
					"emailLabel": "כתובת אימייל", \
					"passwordLabel": "סיסמה סודית" \
				} \
			} \
		}'
