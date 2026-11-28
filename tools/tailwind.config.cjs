/** Tailwind v3 production build config for RatePath (replaces the runtime cdn.tailwindcss.com script). */
module.exports = {
  darkMode: "class",
  content: [
    "./frontend/**/*.html",
    "./frontend/js/**/*.js",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "IBM Plex Sans Arabic", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
    },
  },
  plugins: [],
};
