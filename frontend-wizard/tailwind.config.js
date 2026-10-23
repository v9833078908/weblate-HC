/** @type {import('tailwindcss').Config} */
module.exports = {
  blocklist: ["overline"],
  darkMode: ["class"],
  content: [
    "./pages/**/*.{js,jsx}",
    "./components/**/*.{js,jsx}",
    "./app/**/*.{js,jsx}",
    "./src/**/*.{js,jsx}",
  ],
  prefix: "",
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: { "2xl": "1400px" },
    },
    extend: {
      fontFamily: {
        sans: ['"Source Sans 3"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: [
          '"Source Code Pro"',
          "ui-monospace",
          "SFMono-Regular",
          "monospace",
        ],
      },
      fontSize: {
        "display-lg": [
          "56px",
          { lineHeight: "64px", letterSpacing: "-0.46px", fontWeight: "400" },
        ],
        "metric-lg": [
          "40px",
          { lineHeight: "50px", letterSpacing: "-0.67px", fontWeight: "600" },
        ],
        "heading-page": [
          "24px",
          { lineHeight: "31px", letterSpacing: "-0.4px", fontWeight: "600" },
        ],
        "heading-card": ["18px", { lineHeight: "24px", fontWeight: "600" }],
        "body-md": ["16px", { lineHeight: "24px", fontWeight: "400" }],
        "body-sm": ["14px", { lineHeight: "18px", fontWeight: "400" }],
        "label-strong": ["14px", { lineHeight: "18px", fontWeight: "600" }],
        "label-caps": [
          "12px",
          { lineHeight: "16px", letterSpacing: "0.08em", fontWeight: "600" },
        ],
        "code-md": ["14px", { lineHeight: "22px", fontWeight: "400" }],
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
          strong: "hsl(var(--primary-strong))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        warning: {
          DEFAULT: "hsl(var(--warning))",
          surface: "hsl(var(--warning-surface))",
        },
        info: {
          DEFAULT: "hsl(var(--info))",
          strong: "hsl(var(--info-strong))",
          surface: "hsl(var(--info-surface))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        topbar: {
          DEFAULT: "hsl(var(--topbar))",
          foreground: "hsl(var(--topbar-foreground))",
          "foreground-hover": "hsl(var(--topbar-foreground-hover))",
        },
        highlight: {
          glossary: "hsl(var(--highlight-glossary))",
        },
      },
      borderRadius: {
        sm: "4px",
        DEFAULT: "10px",
        md: "8px",
        lg: "14px",
        xl: "20px",
        full: "9999px",
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
