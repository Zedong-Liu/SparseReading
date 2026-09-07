import react from "@astrojs/react";
import sitemap from "@astrojs/sitemap";
import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://zedong-liu.github.io",
  base: process.env.SPARSEREAD_BASE || "/SparseReading/",
  integrations: [
    react(),
    sitemap({
      filter: (page) => !page.includes("/review"),
    }),
    {
      name: "sparseread-review",
      hooks: {
        "astro:config:setup": ({ command, injectRoute }) => {
          if (command === "dev") {
            injectRoute({
              pattern: "/review",
              entrypoint: "./src/review.astro",
            });
          }
        },
      },
    },
  ],
  trailingSlash: "always",
  vite: {
    server: {
      fs: {
        allow: [".."],
      },
    },
  },
});
