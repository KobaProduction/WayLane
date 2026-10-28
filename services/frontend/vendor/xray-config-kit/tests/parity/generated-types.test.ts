import { describe, expect, it } from "bun:test";
import type {
  XrayParityInboundProtocol,
  XrayParityOutboundProtocol,
  XrayParityReleaseTag,
  XrayParitySecurityType,
  XrayParityTopLevelKey
} from "../../src/index.js";

describe("xray parity generated type exports", () => {
  it("exports generated release, protocol, security, and top-level unions", () => {
    const releaseTag: XrayParityReleaseTag = "v26.5.3";
    const inboundProtocol: XrayParityInboundProtocol<"v26.5.3"> = "vless";
    const outboundProtocol: XrayParityOutboundProtocol<"v26.5.3"> = "freedom";
    const securityType: XrayParitySecurityType<"v26.5.3"> = "reality";
    const topLevelKey: XrayParityTopLevelKey<"v26.5.3"> = "routing";

    expect({ releaseTag, inboundProtocol, outboundProtocol, securityType, topLevelKey }).toEqual({
      releaseTag: "v26.5.3",
      inboundProtocol: "vless",
      outboundProtocol: "freedom",
      securityType: "reality",
      topLevelKey: "routing"
    });
  });
});
