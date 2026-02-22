# Contributing to Keycluster

Thank you for your interest in contributing. This document covers the process
for setting up a development environment, running tests, and submitting changes.

## Development Environment

### Prerequisites

- Python 3.11+
- Docker
- kubectl configured with a local cluster (Minikube or Kind)
- A running Keycloak instance (the walkthrough in `WALKTHROUGH.md` covers this)

### Setup

1. Clone the repository and navigate to it:
   ```bash
   git clone <repo-url>
   cd keycluster
   ```

2. Create a Python virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r services/domain-manager/requirements.txt
   ```

3. Copy the example environment file and fill in your values:
   ```bash
   cp services/domain-manager/.env.example services/domain-manager/.env
   ```

4. Start the local Kubernetes stack (see `WALKTHROUGH.md` for full details):
   ```bash
   minikube start
   minikube addons enable ingress
   ```

## Running Tests

Run the integration test suite:

```bash
make test
```

Run the full test suite (integration + system + security checks):

```bash
make test-full
```

Install test dependencies if needed:

```bash
make install-test-deps
```

## Code Style

- **Python**: Follow PEP 8. Type hints are required on all function signatures.
- **Validation**: All user-facing inputs must be validated through Pydantic models
  with explicit `field_validator` methods.
- **Commits**: Write concise, imperative commit messages (e.g., "Add domain health
  endpoint", not "Added domain health endpoint").
- **No emojis** in code, comments, or documentation.

## Submitting Changes

1. Fork the repository and create a feature branch:
   ```bash
   git checkout -b my-feature
   ```

2. Make your changes. Ensure all tests pass:
   ```bash
   make test
   ```

3. Push and open a pull request against `main`. Describe what your change does and
   why it is needed.

4. A maintainer will review your PR. Address any feedback, then it will be merged.

## Reporting Issues

Open a GitHub issue with:

- A clear title describing the problem.
- Steps to reproduce, including your environment (OS, Python version, K8s version).
- Expected vs. actual behavior.
- Relevant log output (sanitize secrets and internal hostnames).
