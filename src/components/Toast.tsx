import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";

export interface ToastMessage {
  id: string;
  title: string;
  message?: string;
  tone: "success" | "error" | "info";
}

interface ToastProps {
  toasts: ToastMessage[];
  onDismiss: (id: string) => void;
}

export function ToastStack({ toasts, onDismiss }: ToastProps) {
  return (
    <div className="toast-stack" aria-live="polite">
      {toasts.map((toast) => {
        const Icon = toast.tone === "success" ? CheckCircle2 : toast.tone === "error" ? AlertCircle : Info;
        return (
          <div className={`toast ${toast.tone}`} key={toast.id}>
            <Icon size={18} />
            <div>
              <strong>{toast.title}</strong>
              {toast.message && <span>{toast.message}</span>}
            </div>
            <button type="button" aria-label="关闭提示" onClick={() => onDismiss(toast.id)}>
              <X size={15} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
