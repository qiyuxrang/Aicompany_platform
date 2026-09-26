import json

from django.test import SimpleTestCase
from portal.hr_matching import requirements_for, parse_profile, match_matrix, score_matrix


class MatchingTests(SimpleTestCase):
    def test_evidence_and_unknown_are_not_negative_verdicts(self):
        required = requirements_for({'skill_requirements': ['SQL'], 'required_requirements': '交付管理'})
        output = {'requirements': [
            {'requirement_id': required[0]['id'], 'verdict': 'MATCH',
             'evidence': [{'quote': '熟悉SQL', 'locator': 'page:1'}]},
            {'requirement_id': required[1]['id'], 'verdict': 'NOT_MATCH',
             'evidence': [{'quote': '不存在的文本'}]},
        ]}
        matrix = match_matrix(json.dumps(output), required, '熟悉SQL')
        self.assertEqual([row['verdict'] for row in matrix], ['MATCH', 'UNKNOWN'])
        score = score_matrix(matrix)
        self.assertEqual(score['total'], 1)
        self.assertEqual(score['unknown_count'], 1)
        self.assertEqual(score['hard_gap'], [])

    def test_hard_gap_not_offset_by_bonus(self):
        matrix = [{'id': 'hard', 'category': 'hard', 'verdict': 'NOT_MATCH'},
                  {'id': 'bonus', 'category': 'bonus', 'verdict': 'MATCH'}]
        score = score_matrix(matrix)
        self.assertEqual(score['total'], .5)
        self.assertEqual(score['hard_gap'], ['hard'])

    def test_invalid_json_is_explicit_failure(self):
        for value in ('invalid', '[]', '{"requirements": "bad"}'):
            with self.assertRaises(ValueError):
                match_matrix(value, requirements_for({'skill_requirements': ['SQL']}), 'SQL')

    def test_profile_only_keeps_verifiable_extraction(self):
        data = {'name': {'value': '测试人', 'status': 'extracted',
                         'source_ref': {'quote': '姓名：测试人', 'locator': 'line1'}},
                'skills': {'value': ['Python'], 'status': 'extracted',
                           'source_ref': {'quote': '编造'}}}
        profile = parse_profile(json.dumps(data), '姓名：测试人')
        self.assertEqual(profile['name']['status'], 'extracted')
        self.assertEqual(profile['skills']['status'], 'unknown')

    def test_age_notes_never_become_scores_or_exclusions(self):
        required = requirements_for({'education_requirement': '本科',
            'required_requirements': '年龄不超过35岁\n熟悉SQL',
            'preferred_requirements': '30岁以下优先', 'skill_requirements': ['Python', 'age under 35']})
        self.assertEqual([r['text'] for r in required], ['本科', 'Python', '熟悉SQL'])
        legacy_age = {'id': 'age', 'text': '年龄小于35岁', 'category': 'hard', 'verdict': 'NOT_MATCH'}
        score = score_matrix([legacy_age])
        self.assertEqual(score['full_max'], 0)
        self.assertEqual(score['hard_gap'], [])
        self.assertEqual(match_matrix('{"requirements": []}', [legacy_age], '36岁'), [])
