import { cubicOut } from 'svelte/easing';

// Matches --ease-spring cubic-bezier(0.16, 1, 0.3, 1) visually.
// Used with Svelte's slide/fade transitions in sidebar sections.
export const navSlide = { duration: 180, easing: cubicOut };
export const navFade = { duration: 120, easing: cubicOut };
