"""Runs inside the backend container, fed over stdin by check-least-privilege.sh.

Every storage call the application makes, with its real credentials and its real
client, then the things those credentials must not be able to do. Only running
it shows whether a scoped MinIO user can use encrypted uploads, so it runs in CI
and again on the server.
"""

import asyncio
import sys
import uuid

import httpx
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.modules.storage.backend import get_storage_backend

BODY = b"least-privilege check"


async def allowed() -> None:
    backend = get_storage_backend()
    client = backend._client  # noqa: SLF001 -- gdpr.py's calls need the raw client
    run = uuid.uuid4().hex
    incoming, promoted = f"incoming/check-{run}", f"check-{run}"

    upload = await backend.presign_upload(incoming, "text/plain", 1024)
    response = httpx.post(
        upload.url,
        data=upload.fields,
        files={"file": ("check.txt", BODY, "text/plain")},
        timeout=30,
    )
    response.raise_for_status()

    head = await backend.head(incoming)
    assert head is not None, "uploaded object not found"
    await backend.copy_if_match(incoming, promoted, head["ETag"])
    assert await backend.read_prefix(promoted, len(BODY)) == BODY, "read back something else"

    download = httpx.get(await backend.presign_download(promoted), timeout=30)
    download.raise_for_status()
    assert download.content == BODY, "presigned download returned something else"

    bucket = get_settings().AWS_BUCKET_NAME
    client.put_object(Bucket=bucket, Key=f"data-exports/check-{run}", Body=BODY)
    listing = client.list_objects_v2(Bucket=bucket, Prefix=f"data-exports/check-{run}")
    assert [item["Key"] for item in listing["Contents"]] == [f"data-exports/check-{run}"]
    client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": f"data-exports/check-{run}"}]})

    await backend.delete(incoming)
    await backend.delete(promoted)


def refused() -> None:
    client = get_storage_backend()._client  # noqa: SLF001
    try:
        client.create_bucket(Bucket=f"check-{uuid.uuid4().hex}")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        assert code == "AccessDenied", f"create a bucket: expected AccessDenied, got {code}"
    else:
        sys.exit("the application's storage account can create a bucket")
    # MinIO filters this listing rather than refusing it.
    names = [item["Name"] for item in client.list_buckets()["Buckets"]]
    assert names == [get_settings().AWS_BUCKET_NAME], f"it can see the buckets {names}"


asyncio.run(allowed())
refused()
print("storage: allowed calls work, the others are refused")
