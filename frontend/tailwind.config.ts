import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#0a0a0a",
        surface: "#111111",
        border: "#1f1f1f",
        accent: "#f0b90b",
        profit: "#0ecb81",
        loss: "#f6465d",
        muted: "#848e9c",
      },
    },
  },
  plugins: [],
};

export default config;
