/**
 * Design tokens are config, not vibes. These are the exact values from the
 * product spec — the cockpit is intentionally NOT generic Tailwind defaults.
 */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "#F7F8FA",
        card: "#FFFFFF",
        hairline: "#E4E7EC",
        ink: "#12161C",
        muted: "#667085",
        faint: "#98A2B3",
        accent: "#0E7C7B", // deep teal — live/active states + the mark only
        up: "#1A7F4B",
        down: "#B42318",
        // four category colors (index order mirrors the artifact's categories[])
        clinical: "#7C3AED",
        commercial: "#0891B2",
        regulatory: "#D97706",
        financial: "#2563EB",
      },
      fontFamily: {
        display: ["'Space Grotesk'", "system-ui", "sans-serif"],
        body: ["Inter", "system-ui", "sans-serif"],
        mono: ["'IBM Plex Mono'", "ui-monospace", "monospace"],
      },
      borderColor: {
        DEFAULT: "#E4E7EC",
      },
      boxShadow: {
        tile: "0 1px 2px rgba(18,22,28,0.04)",
        lift: "0 4px 16px rgba(18,22,28,0.10)",
      },
      keyframes: {
        pulsering: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
      },
      animation: {
        pulsering: "pulsering 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
