// Throwaway key material for the test, e2e, load-test and prod-compose
// stacks, printed as dotenv lines to append to a copied fixture:
//
//   cp .env.e2e .env && node ../scripts/gen-test-secrets.mjs >> .env
//
// The fixtures carry no keys of their own. A key committed to a public repo
// is a published key whatever it was meant for, and production refuses the
// ones that once were (app/core/config.py). Node rather than openssl or
// Python: it is the one runtime every job here has, the Playwright container
// included.
import { generateKeyPairSync, randomBytes } from "node:crypto";

const { privateKey, publicKey } = generateKeyPairSync("rsa", {
  modulusLength: 2048,
  privateKeyEncoding: { type: "pkcs8", format: "pem" },
  publicKeyEncoding: { type: "spki", format: "pem" },
});

// Fernet: 32 random bytes, url-safe base64 with its padding.
const fernet = `${randomBytes(32).toString("base64url")}=`;

process.stdout.write(
  [
    "",
    `JWT_PRIVATE_KEY="${privateKey.trim()}"`,
    `JWT_PUBLIC_KEY="${publicKey.trim()}"`,
    `ENCRYPTION_KEY=${fernet}`,
    "",
  ].join("\n"),
);
