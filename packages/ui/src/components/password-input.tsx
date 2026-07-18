import { forwardRef, useState } from "react";
import type { ComponentProps } from "react";
import { Eye, EyeOff } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@forge/utils";

import { Input } from "./input";

// T-216/ASVS V2.1.12: a temporary reveal toggle on every password field --
// the icon button never steals the field's own focus/tab position (it's
// just an ordinary tabbable button *after* the input in DOM order) and
// never submits the form (`type="button"`).
export type PasswordInputProps = Omit<ComponentProps<typeof Input>, "type">;

const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  ({ className, ...props }, ref) => {
    const { t } = useTranslation();
    const [visible, setVisible] = useState(false);

    return (
      <div className="relative">
        <Input
          type={visible ? "text" : "password"}
          className={cn("pe-9", className)}
          ref={ref}
          {...props}
        />
        <button
          type="button"
          onClick={() => setVisible((current) => !current)}
          aria-label={
            visible ? t("auth.password_field.hide") : t("auth.password_field.show")
          }
          aria-pressed={visible}
          className="absolute inset-y-0 inset-e-0 flex items-center px-3 text-muted-foreground hover:text-foreground"
        >
          {visible ? (
            <EyeOff className="size-4" aria-hidden="true" />
          ) : (
            <Eye className="size-4" aria-hidden="true" />
          )}
        </button>
      </div>
    );
  },
);
PasswordInput.displayName = "PasswordInput";

export { PasswordInput };
