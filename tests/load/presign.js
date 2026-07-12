import { sleep } from "k6";
import { getSession } from "./lib/session.js";
import { uploadFile } from "./lib/workloads.js";
import { THRESHOLDS } from "./lib/config.js";

// T-223: file presign + confirm loop. Each iteration does the full real
// round trip -- presign, POST the file straight to MinIO with the returned
// policy fields, then confirm -- not just the two backend calls, since
// StorageService.confirm_upload (backend/app/modules/storage/service.py)
// HEADs the object and rejects confirm if nothing was actually uploaded.
export const options = {
  scenarios: {
    presign: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "10s", target: 15 },
        { duration: "30s", target: 15 },
        { duration: "10s", target: 0 },
      ],
    },
  },
  thresholds: THRESHOLDS,
};

export default function () {
  const session = getSession();
  if (!session) {
    sleep(1);
    return;
  }
  uploadFile(session, `${__VU}-${__ITER}`);
  sleep(1);
}
