import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#18212f",
        paper: "#f7f4ed",
        mint: "#2e7d72",
        coral: "#d45d4c",
        marigold: "#d89b2b",
        plum: "#70406f"
      },
      boxShadow: {
        panel: "0 12px 28px rgba(24, 33, 47, 0.08)"
      }
    }
  },
  plugins: []
};

export default config;

