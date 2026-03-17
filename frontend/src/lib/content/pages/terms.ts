import type { ContentPage } from '../types';

export const terms: ContentPage = {
  slug: 'terms',
  title: 'Open Source. Open Terms.',
  description: 'Project Synthesis is free, open source software under the Apache License 2.0. No seat limits, no feature gates, no paid tier.',
  sections: [
    {
      type: 'hero',
      heading: 'OPEN SOURCE. OPEN TERMS.',
      subheading:
        'Apache License 2.0. Free for personal use, team use, and organizational use — without conditions, registration, or license key.',
    },
    {
      type: 'prose',
      blocks: [
        {
          heading: 'License',
          content:
            'All use is governed by the Apache License 2.0. You are free to use, modify, and distribute this software, including for commercial purposes, subject to the terms of that license. A copy of the license is included in the repository root.',
        },
        {
          heading: 'Free for Everyone',
          content:
            'Personal use, team use, and organizational use are all permitted without conditions, registration, or license key. There are no seat limits, no feature gates, and no paid tier.',
        },
        {
          heading: 'No SLA or Vendor Relationship',
          content:
            'Project Synthesis is a community-maintained project. There is no guaranteed response time, no support contract, and no enterprise tier. Zen Resources is the initiating organization, not a vendor. Issues and pull requests are handled by the community on a best-effort basis.',
        },
        {
          heading: 'Sustainability',
          content:
            'The project is sustained by its community. Users who build on Project Synthesis are encouraged — not required — to contribute back via pull requests, bug reports, documentation improvements, or helping others in the issue tracker.',
        },
        {
          heading: 'Contributions',
          content:
            'Contributions are welcome under the same Apache 2.0 license. By submitting a pull request, you agree that your contribution may be distributed under the project license. All contributions are subject to code review for architecture compliance, layer rule adherence, and design system consistency.',
        },
      ],
    },
  ],
};
