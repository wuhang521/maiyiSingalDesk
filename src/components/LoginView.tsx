import { ArrowRight, Eye, EyeOff, LoaderCircle, ShieldCheck } from "lucide-react";
import { useState } from "react";
import mayiLogo from "../assets/mayi-logo.webp";

interface LoginViewProps {
  onLogin: (username: string, password: string) => Promise<void>;
}

export function LoginView({ onLogin }: LoginViewProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password) return;
    setSubmitting(true);
    setError("");
    try {
      await onLogin(username, password);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "登录失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <div className="login-window-drag" aria-hidden="true" />

      <aside className="login-story">
        <div className="login-brand">
          <img src={mayiLogo} alt="" />
          <strong>迈医协同</strong>
        </div>
        <div className="login-story-copy">
          <h2>
            让每一次协作，
            <span>都有清晰的下一步。</span>
          </h2>
          <p>任务、内容与团队进展汇聚在一个安静的工作台里。</p>
        </div>
        <div className="login-story-art" aria-hidden="true">
          <span className="login-orbit login-orbit-outer" />
          <span className="login-orbit login-orbit-inner" />
          <img src={mayiLogo} alt="" />
          <i className="login-orbit-dot login-orbit-dot-one" />
          <i className="login-orbit-dot login-orbit-dot-two" />
        </div>
        <p className="login-story-footer">迈医科技 · 内部智能协作平台</p>
      </aside>

      <section className="login-access" aria-labelledby="login-title">
        <div className="login-panel">
          <div className="login-mobile-brand">
            <img src={mayiLogo} alt="" />
            <strong>迈医协同</strong>
          </div>

          <div className="login-copy">
            <h1 id="login-title">登录工作台</h1>
            <p>使用你的公司账号继续使用迈医协同。</p>
          </div>

          <form className="login-form" onSubmit={submit}>
            <label>
              <span>账号</span>
              <input
                autoFocus
                autoComplete="username"
                value={username}
                placeholder="请输入公司账号"
                disabled={submitting}
                onChange={(event) => setUsername(event.target.value)}
              />
            </label>
            <div className="login-field">
              <label htmlFor="login-password">密码</label>
              <div className="login-password-field">
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  value={password}
                  placeholder="请输入密码"
                  disabled={submitting}
                  onChange={(event) => setPassword(event.target.value)}
                />
                <button
                  type="button"
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  title={showPassword ? "隐藏密码" : "显示密码"}
                  onClick={() => setShowPassword((current) => !current)}
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            {error && <div className="login-error" role="alert">{error}</div>}

            <button
              className="login-submit"
              type="submit"
              disabled={submitting || !username.trim() || !password}
            >
              <span>{submitting ? "正在登录…" : "继续"}</span>
              {submitting
                ? <LoaderCircle className="spin" size={18} />
                : <ArrowRight size={18} />}
            </button>
          </form>

          <div className="login-security">
            <ShieldCheck size={16} />
            <span>登录状态将安全保存在这台设备上。</span>
          </div>
        </div>

        <p className="login-access-footer">仅限迈医科技内部成员使用</p>
      </section>
    </main>
  );
}
