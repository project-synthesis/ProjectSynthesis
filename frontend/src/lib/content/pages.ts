import type { ContentPage } from './types';
import { pipeline } from './pages/pipeline';
import { scoring } from './pages/scoring';
import { refinement } from './pages/refinement';
import { integrations } from './pages/integrations';
import { documentation } from './pages/documentation';
import { apiReference } from './pages/api-reference';
import { mcpServer } from './pages/mcp-server';
import { changelog } from './pages/changelog';
import { about } from './pages/about';
import { blog } from './pages/blog';
import { careers } from './pages/careers';
import { contact } from './pages/contact';
import { privacy } from './pages/privacy';
import { terms } from './pages/terms';
import { security } from './pages/security';

const allPages: Record<string, ContentPage> = {
  pipeline,
  scoring,
  refinement,
  integrations,
  documentation,
  'api-reference': apiReference,
  'mcp-server': mcpServer,
  changelog,
  about,
  blog,
  careers,
  contact,
  privacy,
  terms,
  security,
};

export function getPage(slug: string): ContentPage | undefined {
  return allPages[slug];
}

export function getAllSlugs(): string[] {
  return Object.keys(allPages);
}
