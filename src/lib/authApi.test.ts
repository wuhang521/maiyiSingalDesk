import { afterEach, describe, expect, it, vi } from "vitest";
import { authApi } from "./authApi";

describe("authApi", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("登录后把服务端身份映射为客户端会话", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        access_token: "personal-token",
        expires_at: "2026-11-16T08:00:00",
        user: {
          id: 3,
          username: "boss",
          display_name: "敏总",
          staff_id: 7,
          role: "admin",
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const session = await authApi.login("https://api.example.com", " boss ", "password-2026");

    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.com/api/v1/auth/login",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ username: "boss", password: "password-2026" }),
      }),
    );
    expect(session).toMatchObject({
      accessToken: "personal-token",
      user: { displayName: "敏总", staffId: 7, role: "admin" },
    });
  });
});
