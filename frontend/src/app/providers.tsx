import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { SessionProvider } from "./session";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 数据随运行与节点变化，不做长时间缓存；页面切换范围时会清掉旧键
      staleTime: 15_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <SessionProvider>{children}</SessionProvider>
    </QueryClientProvider>
  );
}
