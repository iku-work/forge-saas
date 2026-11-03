#!/bin/sh
# Run by the minio-init service, inside the MinIO image, which has no grep or sed.
# Safe to run again: `mc mb --ignore-existing` and `ilm import` converge, and
# `user add` and `policy create` overwrite.
set -eu

mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb --ignore-existing "local/$MINIO_BUCKET"

# incoming/ objects expire after a day (review R1: see the same service in
# docker-compose.yml for why, and why `ilm import`).
echo '{"Rules":[{"ID":"expire-incoming","Status":"Enabled","Filter":{"Prefix":"incoming/"},"Expiration":{"Days":1}},{"ID":"expire-data-exports","Status":"Enabled","Filter":{"Prefix":"data-exports/"},"Expiration":{"Days":1}}]}' |
  mc ilm import "local/$MINIO_BUCKET"

# The application's own user: this bucket's objects and nothing else. The root
# user stays on the box for this script and for backups.
echo "{\"Version\":\"2012-10-17\",\"Statement\":[
  {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:PutObject\",\"s3:DeleteObject\"],\"Resource\":[\"arn:aws:s3:::$MINIO_BUCKET/*\"]},
  {\"Effect\":\"Allow\",\"Action\":[\"s3:ListBucket\",\"s3:GetBucketLocation\"],\"Resource\":[\"arn:aws:s3:::$MINIO_BUCKET\"]}
]}" > /tmp/forge-app-policy.json
mc admin policy create local forge-app /tmp/forge-app-policy.json
mc admin user add local "$MINIO_APP_ACCESS_KEY" "$MINIO_APP_SECRET_KEY"

# `policy attach` fails when the policy is already attached.
case "$(mc admin user info local "$MINIO_APP_ACCESS_KEY")" in
  *PolicyName:*forge-app*) ;;
  *) mc admin policy attach local forge-app --user "$MINIO_APP_ACCESS_KEY" ;;
esac
