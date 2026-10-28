const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

function addBase64Padding(value: string): string {
  const remainder = value.length % 4;
  return remainder === 0 ? value : `${value}${"=".repeat(4 - remainder)}`;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let output = "";
  let index = 0;

  for (; index + 2 < bytes.length; index += 3) {
    const chunk = (bytes[index]! << 16) | (bytes[index + 1]! << 8) | bytes[index + 2]!;
    output += alphabet[(chunk >> 18) & 63];
    output += alphabet[(chunk >> 12) & 63];
    output += alphabet[(chunk >> 6) & 63];
    output += alphabet[chunk & 63];
  }

  const remaining = bytes.length - index;
  if (remaining === 1) {
    const chunk = bytes[index]! << 16;
    output += alphabet[(chunk >> 18) & 63];
    output += alphabet[(chunk >> 12) & 63];
    output += "==";
  } else if (remaining === 2) {
    const chunk = (bytes[index]! << 16) | (bytes[index + 1]! << 8);
    output += alphabet[(chunk >> 18) & 63];
    output += alphabet[(chunk >> 12) & 63];
    output += alphabet[(chunk >> 6) & 63];
    output += "=";
  }

  return output;
}

export function base64ToBytes(value: string): Uint8Array {
  const trimmed = value.trim();
  const padded = addBase64Padding(trimmed);

  if (padded.length === 0 || padded.length % 4 !== 0) {
    throw new Error("Invalid base64 value.");
  }
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(padded) || /=/.test(padded.slice(0, -2))) {
    throw new Error("Invalid base64 value.");
  }

  const padding = padded.endsWith("==") ? 2 : padded.endsWith("=") ? 1 : 0;
  const bytes = new Uint8Array((padded.length / 4) * 3 - padding);
  let byteIndex = 0;

  for (let index = 0; index < padded.length; index += 4) {
    const a = alphabet.indexOf(padded[index]!);
    const b = alphabet.indexOf(padded[index + 1]!);
    const c = padded[index + 2] === "=" ? 0 : alphabet.indexOf(padded[index + 2]!);
    const d = padded[index + 3] === "=" ? 0 : alphabet.indexOf(padded[index + 3]!);

    if (a < 0 || b < 0 || c < 0 || d < 0) {
      throw new Error("Invalid base64 value.");
    }

    const chunk = (a << 18) | (b << 12) | (c << 6) | d;
    if (byteIndex < bytes.length) bytes[byteIndex++] = (chunk >> 16) & 255;
    if (byteIndex < bytes.length) bytes[byteIndex++] = (chunk >> 8) & 255;
    if (byteIndex < bytes.length) bytes[byteIndex++] = chunk & 255;
  }

  return bytes;
}

export function normalizeWireGuardKey(value: string, fieldName: string): string {
  const bytes = base64ToBytes(value);
  if (bytes.length !== 32) {
    throw new Error(`Invalid ${fieldName}.`);
  }
  return bytesToBase64(bytes);
}
