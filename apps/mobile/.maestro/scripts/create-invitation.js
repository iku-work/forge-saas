// Setup step for deep-link.yaml (T-222): logs in as the seeded mobile-e2e
// org owner and sends a real invitation through the same endpoint the web
// and mobile "invite a teammate" UI calls. Env vars (BACKEND_URL,
// MOBILE_E2E_OWNER_EMAIL, MOBILE_E2E_OWNER_PASSWORD, MOBILE_E2E_INVITEE_EMAIL)
// come from deep-link.yaml's `env:` block, which Maestro exposes to scripts
// as plain globals.

function postJson(url, body, token) {
  var headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = "Bearer " + token;
  var res = http.post(url, { headers: headers, body: JSON.stringify(body) });
  return JSON.parse(res.body);
}

function getJson(url, token) {
  var headers = {};
  if (token) headers.Authorization = "Bearer " + token;
  var res = http.get(url, { headers: headers });
  return JSON.parse(res.body);
}

var login = postJson(BACKEND_URL + "/api/v1/auth/login", {
  email: MOBILE_E2E_OWNER_EMAIL,
  password: MOBILE_E2E_OWNER_PASSWORD,
});
var accessToken = login.data.access_token;

// The seeded mobile-e2e user owns exactly one org ("Mobile E2E" --
// backend/app/core/seed.py's MOBILE_E2E_ORG_NAME), so it's always data[0].
var orgs = getJson(BACKEND_URL + "/api/v1/orgs", accessToken);
var orgId = orgs.data[0].id;

postJson(
  BACKEND_URL + "/api/v1/orgs/" + orgId + "/invitations",
  { email: MOBILE_E2E_INVITEE_EMAIL, role: "member" },
  accessToken,
);
