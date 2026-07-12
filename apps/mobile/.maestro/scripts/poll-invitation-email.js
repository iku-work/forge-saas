// Paired with create-invitation.js; run inside deep-link.yaml's `retry:`
// block so a not-yet-arrived email just throws and Maestro retries rather
// than this script sleeping in a loop. Mirrors
// apps/web/e2e/helpers/mailhog.ts's waitForLink/decodeBody/htmlParts: same
// MailHog v2 search API, same quoted-printable/base64 decoding, same
// depth-first walk of (possibly multipart) MIME parts. MOBILE_E2E_INVITEE_EMAIL
// and MAILHOG_URL come from deep-link.yaml's `env:` block.

function headerValue(headers, name) {
  return (headers[name] && headers[name][0]) || "";
}

function decodeQuotedPrintable(input) {
  var withoutSoftBreaks = input.replace(/=\r\n|=\n/g, "");
  return withoutSoftBreaks.replace(/=([0-9A-Fa-f]{2})/g, function (_m, hex) {
    return String.fromCharCode(parseInt(hex, 16));
  });
}

// Maestro's runScript is a GraalJS sandbox with no atob/Buffer, so this
// can't lean on either the way a Node script (apps/web/e2e/helpers/mailhog.ts)
// would -- same RFC 4648 table/bit-shifting either has to implement by hand.
var BASE64_CHARS =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

function decodeBase64(input) {
  var clean = input.replace(/[^A-Za-z0-9+/]/g, "");
  var output = "";
  for (var i = 0; i + 4 <= clean.length + 3; i += 4) {
    var c0 = BASE64_CHARS.indexOf(clean.charAt(i));
    var c1 = BASE64_CHARS.indexOf(clean.charAt(i + 1));
    var c2 = BASE64_CHARS.indexOf(clean.charAt(i + 2));
    var c3 = BASE64_CHARS.indexOf(clean.charAt(i + 3));
    var triple =
      ((c0 < 0 ? 0 : c0) << 18) |
      ((c1 < 0 ? 0 : c1) << 12) |
      ((c2 < 0 ? 0 : c2) << 6) |
      (c3 < 0 ? 0 : c3);
    output += String.fromCharCode((triple >> 16) & 0xff);
    if (c2 >= 0) output += String.fromCharCode((triple >> 8) & 0xff);
    if (c3 >= 0) output += String.fromCharCode(triple & 0xff);
  }
  return output;
}

function decodeBody(part) {
  // backend/app/workers/jobs/email.py's EmailMessage.add_alternative() picks
  // quoted-printable for the text/plain part but base64 for text/html (any
  // multipart/alternative message with a non-ASCII part gets one of the two,
  // depending on which alternative python's email package is encoding) --
  // both need handling, same as apps/web/e2e/helpers/mailhog.ts's decodeBody.
  var encoding = headerValue(part.Headers, "Content-Transfer-Encoding").toLowerCase();
  if (encoding === "quoted-printable") {
    return decodeQuotedPrintable(part.Body);
  }
  if (encoding === "base64") {
    return decodeBase64(part.Body);
  }
  return part.Body;
}

function htmlParts(item) {
  var found = [];
  function visit(part) {
    if (headerValue(part.Headers, "Content-Type").toLowerCase().indexOf("text/html") !== -1) {
      found.push(part);
    }
    var children = (part.MIME && part.MIME.Parts) || [];
    for (var i = 0; i < children.length; i++) visit(children[i]);
  }
  var parts = (item.MIME && item.MIME.Parts) || [];
  if (parts.length === 0) {
    return [item.Content];
  }
  for (var i = 0; i < parts.length; i++) visit(parts[i]);
  return found;
}

var res = http.get(
  MAILHOG_URL +
    "/api/v2/search?kind=to&query=" +
    encodeURIComponent(MOBILE_E2E_INVITEE_EMAIL) +
    "&limit=20",
);
var body = JSON.parse(res.body);

var token = null;
for (var i = 0; i < body.items.length && !token; i++) {
  var parts = htmlParts(body.items[i]);
  for (var j = 0; j < parts.length && !token; j++) {
    var html = decodeBody(parts[j]).replace(/&amp;/g, "&");
    var match = /accept-invitation\/([\w.-]+)/.exec(html);
    if (match) token = match[1];
  }
}

if (!token) {
  throw new Error(
    "No invitation email for " + MOBILE_E2E_INVITEE_EMAIL + " yet (mailbox has " +
      body.count + " message(s))",
  );
}

output.invitationToken = token;
