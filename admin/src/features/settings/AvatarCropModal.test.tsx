// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AvatarCropModal, renderAvatarPng } from "./AvatarCropModal";

vi.mock("react-easy-crop", () => ({
  default: ({ image }: { image: string }) => <div data-testid="cropper">{image}</div>,
}));

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AvatarCropModal", () => {
  it("渲染裁剪、缩放、偏移和形状控制", () => {
    render(
      <AvatarCropModal
        image="data:image/png;base64,test"
        onClose={() => undefined}
        onConfirm={() => undefined}
      />,
    );

    expect(screen.getByRole("dialog", { name: "裁剪头像" })).toBeTruthy();
    expect(screen.getByTestId("cropper").textContent).toContain("data:image/png");
    expect(screen.getByText("缩放")).toBeTruthy();
    expect(screen.getByText("X 偏移")).toBeTruthy();
    expect(screen.getByRole("button", { name: "圆形" })).toBeTruthy();
    expect(screen.getByText(/512 × 512 PNG/)).toBeTruthy();
  });

  it("通过 canvas toBlob 输出固定尺寸 PNG", async () => {
    const canvases: Array<{ width: number; height: number }> = [];
    const arc = vi.fn();
    const drawImage = vi.fn();
    const originalCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tagName, options) => {
      if (tagName !== "canvas") return originalCreate(tagName, options);
      const canvas = {
        width: 0,
        height: 0,
        getContext: () => ({ beginPath: vi.fn(), arc, clip: vi.fn(), drawImage }),
        toBlob: (callback: BlobCallback) => callback(new Blob(["png"], { type: "image/png" })),
      };
      canvases.push(canvas);
      return canvas as unknown as HTMLCanvasElement;
    });

    const result = await renderAvatarPng(
      {} as CanvasImageSource,
      { x: 1, y: 2, width: 32, height: 32 },
      "round",
    );

    expect(canvases.map(({ width, height }) => ({ width, height }))).toEqual([
      { width: 512, height: 512 },
    ]);
    expect(result).toMatch(/^data:image\/png;base64,/);
    expect(arc).toHaveBeenCalledTimes(1);
    expect(drawImage).toHaveBeenCalledTimes(1);
  });
});
