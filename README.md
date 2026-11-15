# Dakiya Tracking - DevOps Take-Home

Parcel tracking service. Depots and riders submit scan events, workers process them, and customers retrieve parcel status and scan history.

## Architecture

- **API:** FastAPI service for parcel scans and tracking queries.
- **Queue:** Redis receives scan events.
- **Worker:** Processes queued events and persists them.
- **Database:** PostgreSQL stores parcel status and scan history.
- **Containers:** Docker Compose for local development.
- **Kubernetes:** Helm chart for application deployment.
- **GitOps:** Argo CD Application definitions for staging, production, and retail.
- **CI/CD:** GitHub Actions workflow for tests, image builds, vulnerability scans, and GHCR publishing.

See [DESIGN.md](DESIGN.md) for the proposed production architecture, failure handling, promotion model, offline partner updates, and monitoring thresholds.

## Findings and risk status

| Area | Finding / improvement | Status |
|---|---|---|
| Secrets | Original `.env` was present in Git history. It was removed from the current tracked tree, ignored, and replaced with an example file. Historical credentials still require owner confirmation and rotation before publishing history. | Open - security blocker |
| Compose | Added named PostgreSQL volume, database and Redis health checks, dependency readiness, and internal-only database/Redis ports. | Implemented and tested |
| Data persistence | Migrated existing PostgreSQL data into the named volume and verified an existing parcel record. | Tested |
| Containers | API and worker run as non-root users with hardened runtime settings. | Implemented |
| Worker reliability | Current worker uses Redis list operations. This is not equivalent to durable acknowledged message processing; production should use Redis Streams consumer groups or another durable queue. | Improvement documented |
| Helm | Added environment values, PostgreSQL PVC, secret references, probes, and security contexts. | Lint and render tested |
| Kubernetes | Installed the chart on local kind and tested API-to-Redis-to-worker-to-PostgreSQL traffic. | Tested |
| CI/CD | Workflow includes tests, image builds, Trivy scans, and GHCR publishing steps. | Configured; hosted run pending |
| GitOps | Argo CD staging application deployed from a temporary clean Git snapshot using a local Git daemon. API, worker, PostgreSQL, and Redis are healthy. | Local staging sync tested; production/retail sync pending |
| Promotion | Staging is intended to receive new image versions; production and retail require reviewed Git changes. | Partially implemented; end-to-end promotion pending |
| Drift recovery | Manually scaled staging API from 1 to 2 replicas; Argo CD restored the Git-defined count of 1. Kubernetes events confirmed scale-up and scale-down. | Demonstrated and verified |

## Run locally with Docker Compose

Requirements: Docker Engine and Docker Compose plugin.

Create a local environment file:

```bash
cp .env.example .env
nano .env
```

Set a fresh local `DB_PASSWORD` in `.env`. Do not reuse credentials from the original repository history.

Start the services:

```bash
docker compose up -d --build
docker compose ps
```

Check the API:

```bash
curl -i http://localhost:8080/
```

Submit a scan:

```bash
curl -i -X POST http://localhost:8080/scans \
  -H "Content-Type: application/json" \
  -d '{
    "tracking_id": "LOCAL-TEST-001",
    "status": "in_transit",
    "depot": "Lahore",
    "scanned_at": "2026-11-14T10:20:00"
  }'
```

Retrieve the parcel after the worker has processed the event:

```bash
curl -i http://localhost:8080/parcels/LOCAL-TEST-001
```

Run API tests:

```bash
docker compose exec api python -m pytest -q
```

Validate Compose configuration:

```bash
docker compose config --quiet
```

Stop services without deleting the persistent database volume:

```bash
docker compose down
```

Do not use `docker compose down -v` unless you intentionally want to delete the database volume.

## Local Kubernetes with kind

Requirements: Docker, kind, kubectl, and Helm.

Create a cluster if one does not already exist:

```bash
kind create cluster --name dakiya
kubectl cluster-info --context kind-dakiya
```

Build and load the local images:

```bash
docker build -t dakiya-api:latest ./api
docker build -t dakiya-worker:latest ./worker

kind load docker-image dakiya-api:latest --name dakiya
kind load docker-image dakiya-worker:latest --name dakiya
```

Create the namespace and database secret:

```bash
kubectl create namespace dakiya-local

kubectl create secret generic dakiya-db-secret \
  --from-env-file=<(grep '^DB_PASSWORD=' .env) \
  --namespace dakiya-local
```

Install the chart:

```bash
helm lint ./deploy/helm/dakiya

helm install dakiya ./deploy/helm/dakiya \
  --namespace dakiya-local \
  -f deploy/helm/dakiya/values-local.yaml
```

Check workloads:

```bash
kubectl get pods -n dakiya-local
kubectl get services -n dakiya-local
```

Forward the API port:

```bash
kubectl port-forward service/dakiya-api 8081:80 -n dakiya-local
```

Keep that terminal running. In another terminal, test:

```bash
curl -i http://localhost:8081/

curl -i -X POST http://localhost:8081/scans \
  -H "Content-Type: application/json" \
  -d '{
    "tracking_id": "K8S-TEST-001",
    "status": "in_transit",
    "depot": "Lahore",
    "scanned_at": "2026-11-14T17:30:00"
  }'

curl -i http://localhost:8081/parcels/K8S-TEST-001
```

The successful local test returned HTTP 202 for scan submission and HTTP 200 for parcel retrieval, with the event persisted in PostgreSQL.

## Helm environments

The chart has separate values files:

- `values-staging.yaml`
- `values-production.yaml`
- `values-retail.yaml`

Validate rendering:

```bash
helm lint ./deploy/helm/dakiya

for env in staging production retail; do
  echo "===== $env ====="
  helm template dakiya ./deploy/helm/dakiya \
    -f "deploy/helm/dakiya/values-${env}.yaml"
done
```

Production and retail image tags must be replaced with approved immutable image SHAs before deployment.

### Promotion procedure

1. CI builds and scans the API and worker images, then opens a pull request updating the staging image tags.
2. Review and merge the staging pull request. Argo CD automatically syncs staging.
3. Verify staging health and application behavior before requesting production or retail promotion.
4. Open a separate reviewed Git pull request changing the production and/or retail API and worker tags to the same approved immutable image SHA. Do not rebuild the images for promotion.
5. After approval and merge, manually sync the production Argo CD application. Retail uses automated sync after the reviewed Git change is merged.
6. Verify the deployed image tags and application health in each target environment.

The production and retail promotion procedure is documented but has not yet been executed end to end in this local assessment.


## GitOps bootstrap and validation

The Argo CD Application definitions are in `deploy/argocd/`.

After the repository is safely published and reachable from the cluster, install Argo CD:

```bash
kubectl create namespace argocd

kubectl apply -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

Wait for Argo CD components:

```bash
kubectl wait --for=condition=Available deployment \
  --all -n argocd --timeout=300s
```

Apply the applications:

```bash
kubectl apply -f deploy/argocd/staging.yaml
```

Only after replacing the production and retail image placeholders with approved immutable image SHAs, apply those applications separately:

```bash
kubectl apply -f deploy/argocd/production.yaml
kubectl apply -f deploy/argocd/retail.yaml
```

Inspect application status:

```bash
kubectl get applications -n argocd
```

Local GitOps validation was first completed using a temporary clean snapshot repository served through a local Git daemon, where drift self-healing was demonstrated. Staging was then connected to the GitHub repository. After the reviewed promotion PR was merged, Argo CD reported Synced/Healthy and deployed the promoted immutable API and worker image tags. All four staging pods became ready. The staging API returned HTTP 200, accepted a parcel scan, and returned the parcel successfully. Production/retail sync remains pending.

## Evidence

Hosted CI, image publishing, and the staging promotion have been verified. The following links and results provide evidence:

| Evidence | Link |
|---|---|
| GitHub Actions workflows | [View workflow runs](https://github.com/baig892/dakiya-devops-takehome/actions) — successful CI, image publishing, and staging PR jobs |
| Staging image update / promotion pull request | [PR #1 — merged](https://github.com/baig892/dakiya-devops-takehome/pull/1) |
| Argo CD staging sync | Verified against GitHub; Synced/Healthy. API and worker deployed with image SHA `a400d8a6b4957228993dc772dcea838a15058da7` |
| Successful Kubernetes API and service test | Verified in staging; HTTP 200, parcel scan accepted, parcel retrieval successful |
| Drift self-healing demonstration | Verified locally; Kubernetes events show replicas restored from 2 to 1 |

## AI assistance

I used AI as a supporting tool for troubleshooting technical issues, reviewing errors, and improving project documentation. All suggested commands and configuration changes were validated through my own testing before being applied.

## Time spent

Record actual elapsed time; do not estimate or invent figures.

| Task | Actual time |
|---|---|
| Repository review and security investigation | TODO |
| Docker and Compose improvements | TODO |
| API and worker testing | TODO |
| Helm and Kubernetes deployment | TODO |
| CI/CD and GitOps configuration | TODO |
| Design and documentation | TODO |
| Total | TODO |

## Limitations

- The local kind cluster demonstrates application deployment and end-to-end traffic; it is not a production multi-node HA environment.
- Production database replication and failover are design recommendations, not implemented infrastructure.
- The current worker uses Redis list operations; durable acknowledgement and message reclamation need a production queue design.
- GitHub Actions and GHCR image publishing completed successfully; staging deployment was verified.
- GitHub-based Argo CD staging sync, promoted image deployment, and local drift recovery are verified. Production/retail sync remains pending.
- Credentials found in the original `.env` history still require owner confirmation and rotation if they were real. The sanitized history, with `.env` removed, is the history published to GitHub.
