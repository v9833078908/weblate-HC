import { RouterProvider, type RouterProviderProps } from "react-router-dom";

interface AppProps {
  router: RouterProviderProps["router"];
}

export function App({ router }: AppProps) {
  return <RouterProvider router={router} />;
}
