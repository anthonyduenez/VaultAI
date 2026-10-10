import type { ReactNode } from "react";

interface CardProps {
  title: string;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  id?: string;
  className?: string;
}

export function Card({ title, subtitle, action, children, id, className }: CardProps) {
  return (
    <section className={className ? `card ${className}` : "card"} id={id} aria-label={title}>
      <header className="card-header">
        <div>
          <h2 className="card-title">{title}</h2>
          {subtitle && <p className="card-subtitle">{subtitle}</p>}
        </div>
        {action && <div className="card-action">{action}</div>}
      </header>
      <div className="card-body">{children}</div>
    </section>
  );
}
