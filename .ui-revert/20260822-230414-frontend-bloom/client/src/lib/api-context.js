import { createContext, useContext } from "react";

// The client is threaded through props everywhere it is used deliberately --
// App, the hooks. This context exists for the one place that cannot be reached
// that way: an image buried inside a rendered message, which needs an
// authenticated fetch of its own bytes.
export const ApiContext = createContext(null);

export const useApi = () => useContext(ApiContext);
