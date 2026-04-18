import * as React from "react"
import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

// Utility for cn - assuming this is also in lib/utils but putting it here to self-contain if needed, 
// though we should use the shared one if possible. 
// Actually, let's just use the local one for now to avoid circular deps if utils uses something else.
// But wait, the standard is to import cn from lib/utils. 
// I will import it from there to be consistent with existing codebase.

import { cn } from "@/lib/utils"

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "destructive" | "outline" | "secondary" | "ghost" | "link"
  size?: "default" | "sm" | "lg" | "icon"
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "default", ...props }, ref) => {
    // Basic implementation of variants without CVA
    const baseStyles = "inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50"
    
    const variants = {
      default: "bg-ember-600 text-white hover:bg-ember-700 dark:bg-ember-600 dark:hover:bg-ember-700",
      destructive: "bg-red-500 text-white hover:bg-red-600 dark:bg-red-900 dark:text-red-50 dark:hover:bg-red-900/90",
      outline: "border border-border bg-card hover:bg-muted hover:text-foreground dark:border-border dark:bg-background dark:hover:bg-card dark:hover:text-foreground",
      secondary: "bg-muted text-foreground hover:bg-muted/80 dark:bg-card dark:text-foreground dark:hover:bg-card/80",
      ghost: "hover:bg-muted hover:text-foreground dark:hover:bg-card dark:hover:text-foreground",
      link: "text-ember-600 underline-offset-4 hover:underline dark:text-ember-500",
    }
    
    const sizes = {
      default: "h-10 px-4 py-2",
      sm: "h-9 rounded-md px-3",
      lg: "h-11 rounded-md px-8",
      icon: "h-10 w-10",
    }

    return (
      <button
        className={cn(baseStyles, variants[variant], sizes[size], className)}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button }
