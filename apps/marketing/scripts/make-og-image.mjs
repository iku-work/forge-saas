/**
 * The committed default Open Graph card, drawn from `primaryColor` (T-302).
 *
 * A social card has to be a raster: Facebook, LinkedIn and X all reject SVG,
 * which is why this app's favicon can be generated at build time and this
 * cannot be the same trick. Generating one *per page* at build time is an
 * explicit non-goal of T-302 -- satori or a headless browser is a rendering
 * dependency and a CI browser for a marginal gain over a good default -- so
 * what ships is one committed file and one script that redraws it.
 *
 *     pnpm --filter marketing og-image
 *
 * **Why the card carries no words.** A template does not know the product it
 * will become, and the only text it could draw is `product.name` -- which
 * would mean shipping a font, or faking one, in a repository that has neither.
 * What it can do is be unmistakably this product's colour, in this product's
 * shapes: the rounded square is the favicon's silhouette, the bar inside it is
 * the landing page's `.brand-rule`. `og:image:alt` carries the name in words.
 * A product with a designer replaces public/og-default.png and changes nothing
 * else -- it is referenced from exactly one place, src/config/site.ts.
 *
 * The PNG encoder below is ~40 lines of zlib and CRC32 rather than a
 * dependency: this writes one file, once, in a format whose uncompressed
 * layout is four fields and a filter byte per row.
 */
import { writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { deflateSync, inflateSync } from "node:zlib";
import { fileURLToPath } from "node:url";

import { loadProductConfig } from "./product-config.mjs";

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUTPUT = join(APP_ROOT, "public", "og-default.png");

// Open Graph's canonical 1.91:1. Kept in step with OG_IMAGE_WIDTH/HEIGHT in
// src/config/site.ts, which is what tells the crawler these numbers -- a test
// holds the file to them so the two cannot drift.
const WIDTH = 1200;
const HEIGHT = 630;

/** The card's ground behind the brand colour, and the mark drawn on it. */
const CARD = "#ffffff";

function parseHex(hex) {
  const value = hex.replace("#", "");
  const full =
    value.length === 3
      ? [...value].map((digit) => digit + digit).join("")
      : value;
  return [0, 2, 4].map((offset) => Number.parseInt(full.slice(offset, offset + 2), 16));
}

/**
 * Signed distance from a rounded rectangle: negative inside, positive outside,
 * in pixels. Used instead of sampling a shape N times per pixel because the
 * distance *is* the anti-aliasing -- coverage is `0.5 - d` clamped, which is
 * exact for an edge and close enough for a corner.
 */
function roundedRectDistance(px, py, cx, cy, halfWidth, halfHeight, radius) {
  const dx = Math.abs(px - cx) - (halfWidth - radius);
  const dy = Math.abs(py - cy) - (halfHeight - radius);
  const outside = Math.hypot(Math.max(dx, 0), Math.max(dy, 0));
  return outside + Math.min(Math.max(dx, dy), 0) - radius;
}

function crcTable() {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
}

const CRC_TABLE = crcTable();

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}

/** 8-bit truecolour, no interlacing, filter 0 on every row -- the simplest legal PNG. */
export function encodePng(width, height, rgb) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 2; // colour type: truecolour
  header[10] = 0; // deflate
  header[11] = 0; // adaptive filtering
  header[12] = 0; // no interlace

  const raw = Buffer.alloc(height * (1 + width * 3));
  for (let y = 0; y < height; y += 1) {
    const rowStart = y * (1 + width * 3);
    raw[rowStart] = 0; // filter type: none
    rgb.copy(raw, rowStart + 1, y * width * 3, (y + 1) * width * 3);
  }

  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/**
 * The inverse, for the test that holds the committed file to the brand
 * colour. Only handles what `encodePng` writes -- one IDAT, filter 0 -- and
 * says so loudly rather than silently mis-decoding somebody else's PNG.
 */
export function decodePng(buffer) {
  const width = buffer.readUInt32BE(16);
  const height = buffer.readUInt32BE(20);
  if (buffer[24] !== 8 || buffer[25] !== 2 || buffer[28] !== 0) {
    throw new Error("decodePng: not an 8-bit non-interlaced truecolour PNG");
  }

  const idat = [];
  let offset = 8;
  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.toString("ascii", offset + 4, offset + 8);
    if (type === "IDAT") {
      idat.push(buffer.subarray(offset + 8, offset + 8 + length));
    }
    offset += length + 12;
  }

  const raw = inflateSync(Buffer.concat(idat));
  const pixels = Buffer.alloc(width * height * 3);
  for (let y = 0; y < height; y += 1) {
    const rowStart = y * (1 + width * 3);
    if (raw[rowStart] !== 0) {
      throw new Error(`decodePng: row ${y} uses filter ${raw[rowStart]}, only 0 is handled`);
    }
    raw.copy(pixels, y * width * 3, rowStart + 1, rowStart + 1 + width * 3);
  }
  return { width, height, pixels };
}

/**
 * The card: brand ground, the mark tiled faintly across it, and one solid
 * instance of the mark in the middle with the rule set on the diagonal.
 *
 * Every shape is the same rounded square the favicon draws, at three scales.
 * That is the whole design -- a card that is recognisably one product's
 * without naming it, which is the most a template can honestly do.
 */
export function drawCard(brandColor) {
  const brand = parseHex(brandColor);
  const card = parseHex(CARD);

  const cx = WIDTH / 2;
  const cy = HEIGHT / 2;
  const pixels = Buffer.alloc(WIDTH * HEIGHT * 3);

  // The tiled backdrop: the mark at 1/10 scale, on a staggered grid, barely
  // there. It is what stops the card reading as an icon on a coloured field.
  const CELL = 108;
  const PATTERN_ALPHA = 0.08;

  const SQRT_HALF = Math.SQRT1_2;

  for (let y = 0; y < HEIGHT; y += 1) {
    for (let x = 0; x < WIDTH; x += 1) {
      // Sampled at the pixel's centre, which is what makes the distances
      // symmetric about an even-sized canvas.
      const px = x + 0.5;
      const py = y + 0.5;

      // Nearest cell centre, with every other row offset by half a cell.
      const row = Math.floor(py / CELL);
      const patternCy = (row + 0.5) * CELL;
      const shifted = px + (row % 2 === 0 ? 0 : CELL / 2);
      const patternCx = (Math.floor(shifted / CELL) + 0.5) * CELL - (row % 2 === 0 ? 0 : CELL / 2);
      const pattern =
        coverage(roundedRectDistance(px, py, patternCx, patternCy, 21, 21, 7)) * PATTERN_ALPHA;

      const tile = coverage(roundedRectDistance(px, py, cx, cy, 150, 150, 34));

      // The landing page's `.brand-rule`, turned 45° -- rising, so it reads
      // as a slash rather than as the bar through a "no entry" sign. The
      // sample point is rotated about the tile's centre rather than the
      // shape, which is the same thing and needs no second SDF.
      const dx = px - cx;
      const dy = py - cy;
      const rule = coverage(
        roundedRectDistance(
          (dx - dy) * SQRT_HALF,
          (dy + dx) * SQRT_HALF,
          0,
          0,
          92,
          13,
          13,
        ),
      );

      const offset = (y * WIDTH + x) * 3;
      for (let channel = 0; channel < 3; channel += 1) {
        // Painter's order: ground, pattern, tile, rule. `rule` is only ever
        // drawn where `tile` already is, so it needs no mask of its own.
        let value = brand[channel];
        value = mix(value, card[channel], pattern);
        value = mix(value, card[channel], tile);
        value = mix(value, brand[channel], rule);
        pixels[offset + channel] = Math.round(value);
      }
    }
  }

  return pixels;
}

/** A signed distance turned into how much of this pixel the shape covers. */
function coverage(distance) {
  return Math.min(Math.max(0.5 - distance, 0), 1);
}

function mix(from, to, amount) {
  return from + (to - from) * amount;
}

async function main() {
  const config = await loadProductConfig();
  const png = encodePng(WIDTH, HEIGHT, drawCard(config.primaryColor));
  writeFileSync(OUTPUT, png);
  console.log(
    `make-og-image: wrote ${OUTPUT} (${WIDTH}×${HEIGHT}, ${config.primaryColor}, ${png.length} bytes)`,
  );
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await main();
}
