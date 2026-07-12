import http from "k6/http";
import { check } from "k6";
import { BASE_URL } from "./config.js";
import { authHeaders } from "./auth.js";

// Cursor-follows-cursor: each call re-reads meta.next_cursor from the
// previous response and feeds it into the next request's `after` -- the
// thing that actually exercises pagination instead of always fetching page
// one (backend/app/core/seed_load.py seeds enough rows that every list here
// has well over `maxPages` pages available).
function pageThrough(url, headers, name, maxPages) {
  let after = null;
  for (let page = 0; page < maxPages; page++) {
    const qs = after ? `?after=${after}&limit=20` : `?limit=20`;
    const res = http.get(`${url}${qs}`, { headers, tags: { name } });
    if (!check(res, { [`${name} succeeded`]: (r) => r.status === 200 })) {
      return;
    }
    const cursor = res.json("meta.next_cursor");
    if (!cursor) {
      return;
    }
    after = cursor;
  }
}

export function listMembers(session) {
  pageThrough(
    `${BASE_URL}/orgs/${session.orgId}/members`,
    authHeaders(session.token, session.orgId),
    "list_members",
    3
  );
}

export function listNotifications(session) {
  pageThrough(
    `${BASE_URL}/notifications`,
    authHeaders(session.token, session.orgId),
    "list_notifications",
    3
  );
}

export function listFiles(session) {
  pageThrough(`${BASE_URL}/files`, authHeaders(session.token, session.orgId), "list_files", 3);
}

// text/plain is in storage.service's sniff-exempt set, so this content
// never has to pass libmagic's real signature check against declared
// content_type -- any small deterministic ASCII payload works.
const _FILE_CONTENT = "k6 load test upload\n".repeat(10);

// S3-compatible POST-policy uploads require the file part to be the last
// field on the wire -- passing a plain JS object to http.post() (the
// obvious approach) doesn't guarantee that: k6 marshals it through a Go
// map internally, which iterates in random order, so the same code
// intermittently sent `file` before `key`/policy/signature and MinIO
// rejected it (MalformedPOSTRequest / SignatureDoesNotMatch /
// AuthorizationQueryParametersError, depending on which field landed where
// that particular request -- confirmed by capturing failing response
// bodies against this exact endpoint). Building the multipart body as a
// raw string sidesteps k6's object-to-map conversion entirely, so field
// order is exactly what's written below, every time.
function _buildMultipartUpload(fields, fileContent, fileName, fileContentType) {
  const boundary = `----k6LoadTestBoundary${Math.random().toString(16).slice(2)}`;
  const lines = [];
  for (const [key, value] of Object.entries(fields)) {
    lines.push(`--${boundary}`, `Content-Disposition: form-data; name="${key}"`, "", value);
  }
  lines.push(
    `--${boundary}`,
    `Content-Disposition: form-data; name="file"; filename="${fileName}"`,
    `Content-Type: ${fileContentType}`,
    "",
    fileContent,
    `--${boundary}--`,
    ""
  );
  return { body: lines.join("\r\n"), contentType: `multipart/form-data; boundary=${boundary}` };
}

export function uploadFile(session, tag) {
  const jsonHeaders = Object.assign(
    { "Content-Type": "application/json" },
    authHeaders(session.token, session.orgId)
  );
  const presignRes = http.post(
    `${BASE_URL}/files/presign`,
    JSON.stringify({
      filename: `k6-${tag}.txt`,
      content_type: "text/plain",
      size_bytes: _FILE_CONTENT.length,
      bucket: "default",
    }),
    { headers: jsonHeaders, tags: { name: "presign" } }
  );
  if (!check(presignRes, { "presign succeeded": (r) => r.status === 200 })) {
    return;
  }

  // S3-compatible POST-policy upload: the presign response's `fields` go in
  // verbatim as form fields alongside the file part, straight to
  // `upload_url` -- mirrors backend/tests/test_storage_backend.py's
  // _post_upload, the only other place in the repo that drives this same
  // flow against a real S3-API backend.
  const { file_id: fileId, upload_url: uploadUrl, fields } = presignRes.json("data");
  const formFields = Object.assign({}, fields, { "Content-Type": "text/plain" });
  const { body, contentType } = _buildMultipartUpload(
    formFields,
    _FILE_CONTENT,
    "upload.txt",
    "text/plain"
  );
  const uploadRes = http.post(uploadUrl, body, {
    headers: { "Content-Type": contentType },
    tags: { name: "s3_upload" },
  });
  const uploadOk = check(uploadRes, {
    "s3 upload accepted": (r) => [200, 201, 204].includes(r.status),
  });
  if (!uploadOk) {
    return;
  }

  const confirmRes = http.post(`${BASE_URL}/files/${fileId}/confirm`, null, {
    headers: authHeaders(session.token, session.orgId),
    tags: { name: "confirm" },
  });
  check(confirmRes, { "confirm succeeded": (r) => r.status === 200 });
}
