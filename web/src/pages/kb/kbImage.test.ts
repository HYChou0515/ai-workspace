// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";

import { fileToImageInput } from "./kbImage";

describe("fileToImageInput (#513 P10)", () => {
  it("reads a File into a base64 payload + mime + display name", async () => {
    const bytes = new Uint8Array([1, 2, 3, 255]);
    const file = new File([bytes], "defect.png", { type: "image/png" });

    const img = await fileToImageInput(file);

    expect(img.mime).toBe("image/png");
    expect(img.name).toBe("defect.png");
    // the payload is base64 of the raw bytes and round-trips back to them
    expect(img.data).toBe(btoa(String.fromCharCode(1, 2, 3, 255)));
    expect(atob(img.data)).toBe(String.fromCharCode(1, 2, 3, 255));
  });
});
