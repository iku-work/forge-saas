/*
 * Session management script (T-211) for the staging full-scan plan
 * (.zap/full-scan.yaml).
 *
 * The "json" authentication method (see full-scan.yaml's authentication
 * block) logs in via POST /api/v1/auth/login and gets back an envelope
 * body -- {"data":{"access_token":"...","refresh_token":null,"user":{...}},
 * "error":null,"meta":{...}} -- with the bearer token in the JSON body, not
 * a cookie or a WWW-Authenticate header. ZAP has no built-in session
 * management method that knows how to pull a token out of an arbitrary
 * JSON response and replay it as an Authorization header, so this script
 * fills that gap; the shape (extractWebSession / clearWebSessionIdentifiers
 * / processMessageToMatchSession) is ZAP's documented Session Management
 * Script interface -- see
 * https://github.com/zaproxy/community-scripts/blob/main/session/Juice%20Shop%20Session%20Management.js
 * for the reference implementation this is adapted from (that target also
 * returns a JSON bearer token from its login endpoint; this version drops
 * its extra cookie-replay step since this API only ever checks the
 * Authorization header, never a session cookie, for bearer auth).
 */

function extractWebSession(sessionWrapper) {
    var json = JSON.parse(sessionWrapper.getHttpMessage().getResponseBody().toString());
    var token = json.data.access_token;
    sessionWrapper.getSession().setValue("token", token);
}

function clearWebSessionIdentifiers(sessionWrapper) {
    sessionWrapper.getHttpMessage().getRequestHeader().setHeader("Authorization", null);
}

function processMessageToMatchSession(sessionWrapper) {
    var token = sessionWrapper.getSession().getValue("token");
    if (token === null) {
        print("bearer-session.js: no token on this session yet");
        return;
    }
    sessionWrapper.getHttpMessage().getRequestHeader().setHeader("Authorization", "Bearer " + token);
}

function getRequiredParamsNames() {
    return [];
}

function getOptionalParamsNames() {
    return [];
}
