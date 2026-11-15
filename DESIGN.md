# Dakiya - Production Architecture and Operations Design

## 1. High Availability Architecture

### Dakiya infrastructure

The production design uses two Dakiya application servers in separate failure domains. Both servers run API instances behind a highly available load balancer. Health checks remove an unhealthy instance from traffic. Worker instances run on both servers and consume scan events from a durable queue. Redis Streams consumer groups or a managed durable queue provide pending-message tracking and acknowledgement after successful processing.

The target architecture is:

- **Load balancer:** Routes traffic to healthy API instances and performs health checks.
- **API tier:** Runs multiple stateless API replicas across both servers.
- **Worker tier:** Runs multiple worker replicas. Workers must be safe to retry and must not process the same event twice.
- **Database:** PostgreSQL with a primary and standby in separate failure domains, automated backups, replication monitoring, and tested failover.
- **Queue:** Redis with persistence and a highly available deployment, or a managed durable queue for stronger delivery guarantees.
- **Monitoring:** Collects application, infrastructure, database, queue, and deployment metrics.

The two application servers alone do not provide database quorum or a safe automatic database failover mechanism. Production database failover therefore needs an independent witness/fencing mechanism or a managed HA database service. Backups must be stored outside the two application servers.

### Partner private cloud

The partner environment runs its own application and data services inside its private network. It receives approved, versioned deployment releases from the central delivery process.

The preferred connection is outbound-only from the partner environment. A partner-side deployment agent periodically checks an approved artifact or release endpoint over HTTPS, verifies signatures and checksums, and applies approved updates locally. No inbound connection to the partner network is required.

If the partner site is offline, its current version continues running. New releases remain queued until connectivity returns. The deployment agent reports its deployed version and update status after reconnecting.

## 2. Failure Modes and Zero-Downtime Upgrades

| Failure | Expected behavior and recovery |
|---|---|
| One Dakiya application server fails | Load balancer removes it from service. API and worker capacity continues on the other server, with reduced capacity. |
| API process or container crashes | Health checks mark the instance unhealthy. The orchestrator restarts it and traffic goes to healthy replicas. |
| Worker crashes during processing | With Redis Streams, an unacknowledged event remains pending and can be reclaimed by another worker. Acknowledge only after the database transaction succeeds. Use idempotency keys and bounded retries to prevent duplicate parcel updates. |
| PostgreSQL primary fails | Promote the healthy standby using a controlled failover process with fencing to prevent split brain. Monitor replication lag and verify data consistency. |
| Redis or queue becomes unavailable | API returns a controlled error or applies bounded buffering. Alert on queue availability and backlog. Restore queue service before resuming normal processing. |
| Partner site loses connectivity | Existing services continue locally. Updates wait until outbound connectivity is restored. |

### Zero-downtime release process

1. Build immutable API and worker images, tagged with the Git commit SHA.
2. Run automated tests and vulnerability scans before publishing images.
3. Deploy the new version to staging and run smoke and end-to-end tests.
4. Promote by changing the image SHA in the relevant environment configuration through a reviewed Git change.
5. Use rolling updates with readiness probes and a suitable `maxUnavailable` value. Keep enough healthy old replicas while new replicas become ready.
6. Apply database changes using backward-compatible expand-and-contract migrations. Do not remove old columns or formats until all application versions no longer need them.
7. Verify service health, error rate, and queue processing after rollout. Roll back to the previous image SHA if checks fail.

A two-server deployment must retain enough spare capacity to serve traffic while one server is unavailable. If that capacity is not available, the release must be delayed or traffic reduced safely.

## 3. GitOps and Ten-Client Environment Model

Use one application source and a separate, version-controlled configuration for each client environment.

- `main` contains reviewed application and deployment changes.
- CI builds and scans immutable images, then publishes them to the container registry.
- Staging image updates are promoted through an automated, auditable Git change.
- Production and partner releases require reviewed promotion changes.
- Argo CD continuously reconciles connected environments with their declared Git state. Automated sync and self-healing are enabled only where the environment policy allows them.

For ten clients, maintain a shared Helm chart and ten client-specific values files or configuration overlays. Each client has an explicit image version, replica count, resource settings, secrets references, and feature configuration. Avoid copying and maintaining ten independent charts.

A normal application fix follows this path:

1. Merge the fix into the application source branch.
2. CI tests, scans, builds, and publishes an immutable image.
3. Deploy the new image to staging.
4. Validate the release.
5. Open reviewed promotion changes for the clients approved to receive it.
6. Merge each approved change and let GitOps reconcile that client's environment.

A client that must remain on an older release keeps its previous image SHA in its configuration. Shared chart changes must remain backward-compatible with that pinned release. Track and review exceptions so pinned clients do not silently miss security fixes.

## 4. Offline Partner Updates

The partner environment must not require inbound access from the central platform.

- Publish signed, immutable release bundles containing image references, chart/configuration versions, and checksums.
- The partner-side agent initiates outbound HTTPS connections to check for approved releases.
- Verify release signatures and checksums before applying an update.
- Apply updates locally using the same health checks and rollback rules as connected environments.
- Persist the last known good release and deployment state locally.
- When offline, continue running the last known good version and retain pending update information.
- After reconnecting, report the deployed version, retrieve approved updates, and apply them in order.

If the partner cannot reach the registry, provide a controlled offline transfer process for signed release bundles. Do not bypass signature verification.

## 5. Monitoring and Alert Thresholds

The following are initial operational thresholds. Tune them using production baselines and service-level objectives.

| Alert | Initial threshold | Response |
|---|---|---|
| API availability | Successful health checks below 99.9% over 5 minutes | Check load balancer, API pods, dependencies, and recent releases. |
| API latency | p95 latency above 500 ms for 10 minutes | Inspect database latency, resource saturation, and slow requests. |
| API error rate | HTTP 5xx responses above 2% for 5 minutes | Check application logs, dependencies, and deployment changes. |
| Queue backlog | Oldest unprocessed event older than 2 minutes, or backlog continuously increasing for 5 minutes | Check worker health, Redis/queue availability, and processing failures. |
| Database health | Replication lag above 30 seconds for 5 minutes, or database connections above 80% of configured capacity | Check database load, replication, connection pools, and failover readiness. |

Alerts should include the affected environment, service, severity, dashboard link, and runbook. Critical alerts should page the on-call engineer; warning alerts should create actionable tickets.
