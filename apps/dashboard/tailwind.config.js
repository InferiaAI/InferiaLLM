/** @type {import('tailwindcss').Config} */
export default {
    darkMode: ["class"],
    content: [
        "./pages/**/*.{ts,tsx}",
        "./components/**/*.{ts,tsx}",
        "./app/**/*.{ts,tsx}",
        "./src/**/*.{ts,tsx}",
    ],
    theme: {
        container: {
            center: true,
            padding: "2rem",
            screens: {
                "2xl": "1400px",
            },
        },
        extend: {
            colors: {
                border: "hsl(var(--border))",
                input: "hsl(var(--input))",
                ring: "hsl(var(--ring))",
                background: "hsl(var(--background))",
                foreground: "hsl(var(--foreground))",
                // Editorial palette (matches InferiaAI/new-website)
                cream: {
                    DEFAULT: "#FAF7F2",
                    deep: "#F2EDE4",
                },
                ember: {
                    50:  "#FDF5F0",
                    100: "#F4E8E0",
                    200: "#ECC9B2",
                    300: "#E3AA85",
                    400: "#D98057",
                    500: "#C94D2A",
                    600: "#B84525",
                    700: "#A33D20",
                    800: "#82321A",
                    900: "#5F2616",
                    950: "#3A1710",
                },
                fg: {
                    DEFAULT: "#0C0C0C",
                    secondary: "#3D3D3D",
                    muted: "#8A8A8A",
                },
                primary: {
                    DEFAULT: "hsl(var(--primary))",
                    foreground: "hsl(var(--primary-foreground))",
                },
                secondary: {
                    DEFAULT: "hsl(var(--secondary))",
                    foreground: "hsl(var(--secondary-foreground))",
                },
                destructive: {
                    DEFAULT: "hsl(var(--destructive))",
                    foreground: "hsl(var(--destructive-foreground))",
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
            },
            borderRadius: {
                lg: "var(--radius)",
                md: "calc(var(--radius) - 2px)",
                sm: "calc(var(--radius) - 4px)",
            },
            fontFamily: {
                sans: ["Inter", "sans-serif"],
                serif: ["'DM Serif Display'", "Georgia", "serif"],
                mono: ["JetBrains Mono", "monospace"],
            },
        },
    },
    plugins: [require("tailwindcss-animate")],
}
