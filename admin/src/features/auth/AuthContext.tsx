import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useState,
} from "react";
import { TOKEN_KEY } from "../../api/client";
import { fetchMe, revokeCurrentSession } from "../../api/auth";
import type { CurrentUser } from "../../types/auth";

interface AuthState {
  user: CurrentUser | null;
  checking: boolean;
  setUser: (user: CurrentUser | null) => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  user: null,
  checking: true,
  setUser: () => {},
  logout: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [checking, setChecking] = useState(Boolean(localStorage.getItem(TOKEN_KEY)));

  useEffect(() => {
    const unauthorized = () => setUser(null);
    window.addEventListener("hlg:unauthorized", unauthorized);
    if (!localStorage.getItem(TOKEN_KEY)) {
      setChecking(false);
      return () => window.removeEventListener("hlg:unauthorized", unauthorized);
    }
    fetchMe()
      .then(setUser)
      .catch(() => localStorage.removeItem(TOKEN_KEY))
      .finally(() => setChecking(false));
    return () => window.removeEventListener("hlg:unauthorized", unauthorized);
  }, []);

  const logout = async () => {
    try {
      if (localStorage.getItem(TOKEN_KEY)) await revokeCurrentSession();
    } catch {
      // 网络故障时仍必须清理浏览器内的会话材料。
    } finally {
      localStorage.removeItem(TOKEN_KEY);
      setUser(null);
    }
  };

  return (
    <AuthContext.Provider value={{ user, checking, setUser, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
