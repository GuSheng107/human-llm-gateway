import { useState } from "react";
import Cropper, { type Area } from "react-easy-crop";
import { Modal } from "../../components/feedback/Modal";
import { Button } from "../../components/ui/Button";
import { friendlyErrorMessage } from "../../utils/notify";

export type AvatarCropShape = "round" | "rect";

interface Props {
  image: string;
  onClose: () => void;
  onConfirm: (dataUrl: string) => Promise<void> | void;
}

const OUTPUT_SIZE = 512;

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("头像编码失败"));
    reader.readAsDataURL(blob);
  });
}

function canvasToPng(canvas: HTMLCanvasElement): Promise<string> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error("头像输出失败"));
        return;
      }
      void blobToDataUrl(blob).then(resolve, reject);
    }, "image/png");
  });
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("图片加载失败"));
    image.src = src;
  });
}

export async function renderAvatarPng(
  image: CanvasImageSource,
  crop: Area,
  shape: AvatarCropShape,
): Promise<string> {
  const canvas = document.createElement("canvas");
  canvas.width = OUTPUT_SIZE;
  canvas.height = OUTPUT_SIZE;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("浏览器不支持头像裁剪");
  if (shape === "round") {
    context.beginPath();
    context.arc(OUTPUT_SIZE / 2, OUTPUT_SIZE / 2, OUTPUT_SIZE / 2, 0, Math.PI * 2);
    context.clip();
  }
  context.drawImage(
    image,
    crop.x,
    crop.y,
    crop.width,
    crop.height,
    0,
    0,
    OUTPUT_SIZE,
    OUTPUT_SIZE,
  );
  return canvasToPng(canvas);
}

export function AvatarCropModal({ image, onClose, onConfirm }: Props) {
  const [crop, setCrop] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [shape, setShape] = useState<AvatarCropShape>("round");
  const [pixels, setPixels] = useState<Area | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const confirm = async () => {
    if (!pixels) return;
    setSaving(true);
    setError("");
    try {
      const source = await loadImage(image);
      await onConfirm(await renderAvatarPng(source, pixels, shape));
    } catch (caught) {
      setError(friendlyErrorMessage(caught, "头像处理失败"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title="裁剪头像"
      description="拖动图片调整位置，滚轮或滑块控制缩放"
      onClose={onClose}
      width="max-w-4xl"
    >
      <div className="grid md:grid-cols-[minmax(0,1fr)_17rem]">
        <div className="relative h-[22rem] min-h-72 bg-slate-950 md:h-[32rem]">
          <Cropper
            image={image}
            crop={crop}
            zoom={zoom}
            aspect={1}
            cropShape={shape}
            showGrid={shape === "rect"}
            onCropChange={setCrop}
            onZoomChange={setZoom}
            onCropComplete={(_, areaPixels) => setPixels(areaPixels)}
          />
        </div>
        <div className="space-y-5 border-t border-slate-200 p-5 md:border-l md:border-t-0">
          <label className="block">
            <span className="mb-2 flex justify-between text-xs font-medium text-slate-600">
              <span>缩放</span><span>{zoom.toFixed(1)}×</span>
            </span>
            <input
              type="range"
              min={1}
              max={3}
              step={0.1}
              value={zoom}
              onChange={(event) => setZoom(Number(event.target.value))}
              className="w-full accent-primary"
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs text-slate-600">
              <span className="mb-1 block">X 偏移</span>
              <input
                type="number"
                min={-100}
                max={100}
                value={Math.round(crop.x)}
                onChange={(event) => setCrop({ ...crop, x: Number(event.target.value) })}
                className="field-input"
              />
            </label>
            <label className="text-xs text-slate-600">
              <span className="mb-1 block">Y 偏移</span>
              <input
                type="number"
                min={-100}
                max={100}
                value={Math.round(crop.y)}
                onChange={(event) => setCrop({ ...crop, y: Number(event.target.value) })}
                className="field-input"
              />
            </label>
          </div>
          <fieldset>
            <legend className="mb-2 text-xs font-medium text-slate-600">裁剪形状</legend>
            <div className="grid grid-cols-2 gap-2">
              {(["round", "rect"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setShape(value)}
                  className={`rounded-md border px-3 py-2 text-xs ${
                    shape === value
                      ? "border-primary bg-primary-soft text-primary"
                      : "border-slate-200 text-slate-500"
                  }`}
                >
                  {value === "round" ? "圆形" : "方形"}
                </button>
              ))}
            </div>
          </fieldset>
          <p className="text-xs text-slate-400">输出为 {OUTPUT_SIZE} × {OUTPUT_SIZE} PNG</p>
          {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
          <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
            <Button variant="ghost" onClick={onClose}>取消</Button>
            <Button onClick={() => void confirm()} loading={saving} disabled={!pixels}>
              确定
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
