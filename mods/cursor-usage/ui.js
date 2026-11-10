// Card language borrowed from the Insights mod: theme colors, one radius
// scale, and a short type hierarchy. No heatmap, settings, or export.
import { div } from "gpui-kit";
import { v_flex, h_flex, Button } from "gpui-base";

export const R = { control: 6, card: 8 };
export const H = { control: 30 };
export const G = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 };
export const T = { page: 20, card: 13.5, metric: 28, body: 12.5, small: 11.5, label: 9.5 };

export function tokens(theme) {
  const background = theme.background;
  const canvas = theme.canvas ?? background;
  const card = theme.surface ?? canvas;
  const secondary = theme.raised ?? card;
  return {
    background,
    canvas,
    card,
    primary: theme.solid ?? theme.foreground,
    primaryForeground: theme.solid_foreground ?? background,
    secondary,
    mutedForeground: theme.muted ?? theme.dim,
    subtle: theme.hover ?? secondary,
    accent: theme.accent,
    destructive: theme.blocked ?? theme.ansi1,
    border: theme.border ?? theme.input,
    input: theme.input ?? theme.border,
    ring: theme.accent,
    text: theme.foreground,
    dim: theme.dim,
  };
}

export function metaLabel(text, c) {
  return div().text_size(T.label).font_medium().text_color(c.dim).child(String(text).toUpperCase());
}

export function card(c) {
  return v_flex().w_full().min_w(0).p(G.lg).gap(G.md).rounded(R.card).bg(c.card)
    .border_1().border_color(c.border);
}

export function button(id, label, onClick, c, options = {}) {
  const variant = options.variant ?? "outline";
  const disabled = Boolean(options.disabled);
  const primary = variant === "primary";
  const foreground = primary ? c.primaryForeground : c.text;
  let el = Button.new(id).h(H.control).px(10).items_center().justify_center().rounded(R.control)
    .cursor_pointer()
    .on_click((event, cx) => { if (!disabled) onClick(event, cx); })
    .disabled(disabled)
    .accessibility_label(typeof label === "string" ? label : "Button");
  el = primary
    ? el.bg(c.primary).border_1().border_color(c.primary)
    : el.bg(c.card).border_1().border_color(c.border);
  return el.opacity(disabled ? 0.5 : 1)
    .when(!disabled, (root) => root.hover((style) => {
      if (primary) style.opacity(0.92);
      else style.bg(c.subtle);
    }))
    .focus((style) => style.border_color(c.ring))
    .child(div().text_size(T.small).font_medium().text_color(foreground).child(label));
}

export function stateCard(c, options) {
  const tone = options.tone ?? c.accent;
  return v_flex().w_full().p(G.xl).gap(G.sm).rounded(R.card).bg(c.card)
    .border_1().border_color(tone === c.destructive ? c.destructive : c.border)
    .child(h_flex().items_center().gap(G.sm)
      .child(div().size(8).flex_none().rounded_full().bg(tone))
      .child(div().text_size(T.card).font_semibold().text_color(c.text).child(options.title)))
    .when(Boolean(options.body), (el) => el.child(div().text_size(T.small).text_color(c.mutedForeground).child(options.body)))
    .when(Boolean(options.action), (el) => el.child(h_flex().pt(G.xs).child(options.action)));
}

export function statCell(label, display, c, color) {
  return v_flex().flex_1().min_w(76).gap(G.xs)
    .child(metaLabel(label, c))
    .child(div().text_size(T.body).font_medium().text_color(color ?? c.text).truncate()
      .tooltip(String(display)).child(display));
}
