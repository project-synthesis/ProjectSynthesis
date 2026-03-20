import { describe, expect, it } from 'vitest';
import { calculateDomainAngles, calculateFamilyPositions, calculateEdgePathDistortion } from './layout';
import type { GraphFamily } from '../../../api/patterns';

describe('RadialMindmap Layout Logic', () => {

  const mockFamilies: GraphFamily[] = [
    {
      id: 'fam1', intent_label: 'Intent 1', domain: 'frontend', task_type: 'coding', 
      member_count: 5, usage_count: 10, avg_score: 8.5, meta_patterns: [], created_at: new Date().toISOString()
    },
    {
      id: 'fam2', intent_label: 'Intent 2', domain: 'backend', task_type: 'coding', 
      member_count: 2, usage_count: 5, avg_score: 9.0, meta_patterns: [], created_at: new Date().toISOString()
    },
    {
      id: 'fam3', intent_label: 'Intent 3', domain: 'frontend', task_type: 'debugging', 
      member_count: 8, usage_count: 20, avg_score: 7.5, meta_patterns: [], created_at: new Date().toISOString()
    }
  ];

  describe('calculateDomainAngles', () => {
    it('properly groups families by domain and calculates angles', () => {
      const { domains, domainMap, domainAngles } = calculateDomainAngles(mockFamilies);
      
      expect(domains).toContain('frontend');
      expect(domains).toContain('backend');
      
      expect(domainMap.get('frontend')).toHaveLength(2);
      expect(domainMap.get('backend')).toHaveLength(1);
      
      const frontendAngles = domainAngles.get('frontend');
      const backendAngles = domainAngles.get('backend');
      
      expect(frontendAngles).toBeDefined();
      expect(backendAngles).toBeDefined();
      
      const gap = 0.1;
      const totalSweep = (frontendAngles!.end - frontendAngles!.start) + (backendAngles!.end - backendAngles!.start) ;
      expect(totalSweep).toBeCloseTo(Math.PI * 2, 4);
    });

    it('handles empty family array gracefully', () => {
      const { domains, domainMap, domainAngles } = calculateDomainAngles([]);
      
      expect(domains).toHaveLength(0);
      expect(domainMap.size).toBe(0);
      expect(domainAngles.size).toBe(0);
    });
  });

  describe('calculateFamilyPositions', () => {
    it('calculates xy positions for families within domain angles', () => {
      const { domains, domainMap, domainAngles } = calculateDomainAngles(mockFamilies);
      const cx = 400;
      const cy = 300;
      const radius = 200;
      
      const positions = calculateFamilyPositions(domains, domainMap, domainAngles, cx, cy, radius);
      
      expect(positions.size).toBe(3); 
      const pos1 = positions.get('fam1');
      const pos2 = positions.get('fam2');
      const pos3 = positions.get('fam3');
      
      expect(pos1).toBeDefined();
      expect(pos2).toBeDefined();
      expect(pos3).toBeDefined();
    });
  });

  describe('calculateEdgePathDistortion', () => {
    it('generates a valid SVG quadratic bezier path string', () => {
      const from = { x: 100, y: 100 };
      const to = { x: 200, y: 200 };
      const cx = 0;
      const cy = 0;
      const tension = 0.5;
      
      const path = calculateEdgePathDistortion(from, to, cx, cy, tension);
      expect(path).toMatch(/^M100,100 Q-?[\d.]+,-?[\d.]+ 200,200$/);
    });
  });
});
