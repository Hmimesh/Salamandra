import { ArrowRight, Menu, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { BrandMark } from "./BrandMark";

export function PublicLayout() {
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname, location.hash]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const closeMenu = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        menuButton.current?.focus();
      }
    };
    document.addEventListener("keydown", closeMenu);
    return () => document.removeEventListener("keydown", closeMenu);
  }, [menuOpen]);

  return (
    <div className="public-site">
      <header className="public-header">
        <div className="public-container public-header-inner">
          <Link className="public-brand" to="/" aria-label="Salamandra home">
            <BrandMark />
            <span><strong>SALAMANDRA</strong><small>EVENT OPERATIONS</small></span>
          </Link>
          <nav className={`public-nav ${menuOpen ? "open" : ""}`} id="public-navigation" aria-label="Public navigation">
            <Link to="/#product" onClick={() => setMenuOpen(false)}>Product</Link>
            <Link to="/#workflow" onClick={() => setMenuOpen(false)}>How it works</Link>
            <Link to="/contact" onClick={() => setMenuOpen(false)}>Contact</Link>
            <Link className="public-mobile-cta" to="/register" onClick={() => setMenuOpen(false)}>Create workspace<ArrowRight size={16} /></Link>
          </nav>
          <div className="public-header-actions">
            {location.pathname !== "/login" ? <Link className="public-text-link" to="/login">Sign in</Link> : null}
            {location.pathname !== "/register" ? (
              <Link className="button button-primary" to="/register">
                Create workspace<ArrowRight size={16} />
              </Link>
            ) : null}
            <button
              ref={menuButton}
              className="public-menu-button"
              type="button"
              aria-label={menuOpen ? "Close navigation" : "Open navigation"}
              aria-expanded={menuOpen}
              aria-controls="public-navigation"
              onClick={() => setMenuOpen((open) => !open)}
            >
              {menuOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>
        </div>
      </header>
      <Outlet />
      <footer className="public-footer">
        <div className="public-container public-footer-inner">
          <div className="public-footer-brand"><BrandMark /><span><strong>SALAMANDRA</strong><small>EVENT OPERATIONS</small></span></div>
          <nav aria-label="Footer navigation">
            <Link to="/terms">Terms</Link>
            <Link to="/privacy">Privacy</Link>
            <Link to="/contact">Contact</Link>
          </nav>
          <span>Private staging. Test data only.</span>
        </div>
      </footer>
    </div>
  );
}
