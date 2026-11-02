# InferiaLLM Helm chart

The chart deploys the unified InferiaLLM application with either bundled
Postgres/Redis (evaluation) or externally managed services (production). The
application image is deliberately versioned through `image.tag`; pin a digest
for production rather than relying on `latest`.

```sh
helm upgrade --install inferiallm ./deploy/helm/inferiallm \
  --namespace inferiallm --create-namespace --wait
```

## Backup and restore

Backups are disabled by default. Enabling them creates a dedicated PVC and a
CronJob that writes compressed PostgreSQL custom-format dumps. Use a storage
class with off-cluster replication/retention; the chart's job-history limits do
not prune files from the PVC.

```sh
helm upgrade --install inferiallm ./deploy/helm/inferiallm \
  --namespace inferiallm --create-namespace --wait \
  --set backup.enabled=true

# Run a backup immediately instead of waiting for the schedule.
kubectl -n inferiallm create job --from=cronjob/inferiallm-backup inferiallm-backup-manual
kubectl -n inferiallm wait --for=condition=complete job/inferiallm-backup-manual --timeout=10m
```

Restore is intentionally explicit. First suspend writes (scale the application
to zero or take it out of service), select a dump from the backup PVC, and run
the restore job below. It drops/recreates objects in the target database; use a
fresh restore namespace/database for a rehearsal.

```sh
kubectl -n inferiallm scale deployment/inferiallm --replicas=0

kubectl -n inferiallm run inferiallm-restore --rm -it --restart=Never \
  --image=postgres:15 \
  --overrides='{"spec":{"volumes":[{"name":"backups","persistentVolumeClaim":{"claimName":"inferiallm-backups"}}],"containers":[{"name":"inferiallm-restore","image":"postgres:15","stdin":true,"tty":true,"volumeMounts":[{"name":"backups","mountPath":"/backups"}]}]}}' \
  -- sh
# Inside the pod: provide PGPASSWORD securely, then:
# gunzip -c /backups/inferiallm-<timestamp>.dump.gz | pg_restore --clean --if-exists --no-owner \
#   --host=<postgres-host> --username=<user> --dbname=<database>
```

Restart the deployment only after validating the restored database. For an
external database, replace the host/user/database values with the managed
service credentials and keep backups in its managed backup system where
possible.

## Air-gapped model cache

Set `app.modelEgress=false`, import model weights before isolating the cluster,
and run the existing end-to-end harness from a Docker deployment:

```sh
./scripts/airgap_check.sh stage1
./scripts/airgap_check.sh stage1-ollama
# GPU host only:
./scripts/airgap_check.sh stage2 <model-id>
```

The harness proves an isolated client can retrieve only an imported model from
InferiaLLM's mirror. It does not package container images or model files into a
tarball; registry/image mirroring is an operations responsibility until the
planned `inferiallm bundle` command is implemented.
