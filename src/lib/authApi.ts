import type { AuthSession, AuthUser } from "../types";

interface ServerUser {
  id: number;
  username: string;
  display_name: string;
  staff_id: number;
  role: "admin" | "member";
}

interface LoginResponse {
  access_token: string;
  expires_at: string;
  user: ServerUser;
}

function endpoint(backendUrl: string, path: string): string {
  return `${backendUrl.trim().replace(/\/+$/, "")}${path}`;
}

function userFromServer(user: ServerUser): AuthUser {
  return {
    id: user.id,
    username: user.username,
    displayName: user.display_name,
    staffId: user.staff_id,
    role: user.role,
  };
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `登录服务返回 HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      // 保留 HTTP 状态作为错误信息。
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const authApi = {
  async login(backendUrl: string, username: string, password: string): Promise<AuthSession> {
    const response = await fetch(endpoint(backendUrl, "/api/v1/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username.trim(), password }),
    });
    const result = await parseResponse<LoginResponse>(response);
    return {
      backendUrl,
      accessToken: result.access_token,
      expiresAt: result.expires_at,
      user: userFromServer(result.user),
    };
  },

  async me(session: AuthSession): Promise<AuthUser> {
    const response = await fetch(endpoint(session.backendUrl, "/api/v1/auth/me"), {
      headers: { Authorization: `Bearer ${session.accessToken}` },
    });
    return userFromServer(await parseResponse<ServerUser>(response));
  },

  async logout(session: AuthSession): Promise<void> {
    const response = await fetch(endpoint(session.backendUrl, "/api/v1/auth/logout"), {
      method: "POST",
      headers: { Authorization: `Bearer ${session.accessToken}` },
    });
    await parseResponse<void>(response);
  },
};
