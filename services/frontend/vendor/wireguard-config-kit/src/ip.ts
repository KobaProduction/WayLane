function parsePrefix(prefix: string | undefined, max: number): number | undefined {
  if (prefix === undefined) return max;
  if (!/^\d+$/.test(prefix)) return undefined;
  const value = Number(prefix);
  return Number.isInteger(value) && value >= 0 && value <= max ? value : undefined;
}

function parseIpv4Address(address: string): string | undefined {
  const parts = address.split(".");
  if (parts.length !== 4) return undefined;

  const normalized = parts.map((part) => {
    if (!/^\d+$/.test(part)) return undefined;
    if (part.length > 1 && part.startsWith("0")) return undefined;
    const value = Number(part);
    return Number.isInteger(value) && value >= 0 && value <= 255 ? String(value) : undefined;
  });

  return normalized.every((part): part is string => typeof part === "string")
    ? normalized.join(".")
    : undefined;
}

function ipv6HextetCount(section: string): number | undefined {
  if (section === "") return 0;

  const hextets = section.split(":");
  let count = 0;

  for (let index = 0; index < hextets.length; index += 1) {
    const hextet = hextets[index]!;
    if (hextet === "") return undefined;

    if (hextet.includes(".")) {
      if (index !== hextets.length - 1) return undefined;
      if (!parseIpv4Address(hextet)) return undefined;
      count += 2;
      continue;
    }

    if (!/^[0-9a-fA-F]{1,4}$/.test(hextet)) return undefined;
    count += 1;
  }

  return count;
}

function isValidIpv6Address(address: string): boolean {
  if (address.includes(":::")) return false;

  const compressed = address.includes("::");
  const sections = address.split("::");
  if (sections.length > 2) return false;

  const left = ipv6HextetCount(sections[0] ?? "");
  const right = ipv6HextetCount(sections[1] ?? "");
  if (left === undefined || right === undefined) return false;

  const count = left + right;
  return compressed ? count < 8 : count === 8;
}

export function normalizeIpInterface(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) throw new Error("Address entries must be valid IP interface strings.");

  const parts = trimmed.split("/");
  if (parts.length > 2) throw new Error("Address entries must be valid IP interface strings.");

  const [address, prefixText] = parts as [string, string | undefined];
  const ipv4 = parseIpv4Address(address);
  if (ipv4) {
    const prefix = parsePrefix(prefixText, 32);
    if (prefix === undefined) throw new Error("Address entries must be valid IP interface strings.");
    return `${ipv4}/${prefix}`;
  }

  if (isValidIpv6Address(address)) {
    const prefix = parsePrefix(prefixText, 128);
    if (prefix === undefined) throw new Error("Address entries must be valid IP interface strings.");
    return `${address.toLowerCase()}/${prefix}`;
  }

  throw new Error("Address entries must be valid IP interface strings.");
}
