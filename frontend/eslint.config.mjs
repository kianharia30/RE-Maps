// ESLint 9 flat config.
//
// `next lint` was removed in Next.js 16, so linting runs ESLint directly.
// eslint-config-next still ships the legacy (eslintrc) format, so FlatCompat
// bridges it into the flat config ESLint 9 expects.
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
});

export default [
  {
    ignores: [
      ".next/**",
      "out/**",
      "node_modules/**",
      "next-env.d.ts",
      "eslint.config.mjs",
    ],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
];
