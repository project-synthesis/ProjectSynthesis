import { error } from '@sveltejs/kit';
import { getPage } from '$lib/content/pages';
import type { PageLoad } from './$types';

export const load: PageLoad = ({ params }) => {
  const page = getPage(params.slug);
  if (!page) throw error(404, 'Page not found');
  return { page };
};
