"""Read-only negative controls for native clone receipt verification."""
import unittest
from unittest.mock import patch

import capture_session_clone_run as runner


ROOT = runner.OUTPUT / 'followup-objects-dirty-final'


def evaluate_with(mutate):
    original = runner.read

    def altered(root, name):
        value = original(root, name)
        if name == 'reopened':
            mutate(value)
        return value

    with patch.object(runner, 'read', altered):
        return runner.evaluate(ROOT)


class NativeMappingGuards(unittest.TestCase):
    def test_fixture_exact_semantics(self):
        self.assertTrue(all(runner.evaluate(ROOT)['checks'].values()))

    def test_vertex_delta_rejected(self):
        def mutate(state):
            state['entities'][0]['vertices'][1][0] += 0.01
        result = evaluate_with(mutate)
        self.assertFalse(result['checks']['every_source_instance_has_unique_native_mapping_and_identical_payload'])

    def test_matrix_delta_rejected(self):
        def mutate(state):
            next(e for e in state['entities'] if 'block_transform' in e)['block_transform'][0][3] += 1
        self.assertFalse(evaluate_with(mutate)['checks']['every_source_instance_has_unique_native_mapping_and_identical_payload'])

    def test_archive_local_btr_mismatch_rejected(self):
        def mutate(state):
            next(e for e in state['entities'] if e['route'] == '13/7C')['reference_original_handle'] = 'FFFF'
        result = evaluate_with(mutate)
        self.assertFalse(result['checks']['every_source_instance_has_unique_native_mapping_and_identical_payload'])
        self.assertFalse(result['checks']['every_xref_record_matches_native_mapping'])

    def test_forwarded_btr_mismatch_rejected(self):
        def mutate(state):
            next(e for e in state['entities'] if e['route'] == '13/7C')['reference_redirected_runtime_id'] = '0'
        self.assertFalse(evaluate_with(mutate)['checks']['every_xref_record_matches_native_mapping'])

    def test_missing_instance_rejected(self):
        def mutate(state):
            state['entities'].pop()
        result = evaluate_with(mutate)
        self.assertFalse(result['checks']['every_source_instance_has_unique_native_mapping_and_identical_payload'])
        self.assertFalse(result['checks']['no_unmapped_archive_instances'])

    def test_native_graph_status_delta_rejected(self):
        def mutate(state):
            next(n for n in state['graph']['nodes'] if n['name'] == 'NESTED')['status'] = 4
        result = evaluate_with(mutate)
        self.assertFalse(result['checks']['xref_graph_roles_and_statuses_match'])
        self.assertFalse(result['checks']['every_xref_record_matches_native_mapping'])

    def test_clean_metadata_delta_not_masked(self):
        result = runner.evaluate(runner.OUTPUT / 'followup-objects-clean-final')
        self.assertFalse(result['checks']['live_state_unchanged_at_every_observed_stage'])
        self.assertTrue(result['source_state_deltas'])

    def test_archive_host_filename_is_not_xref_identity(self):
        def mutate(state):
            state['graph']['nodes'][0]['name'] = 'independent-native-archive'
        self.assertTrue(evaluate_with(mutate)['checks']['xref_graph_roles_and_statuses_match'])

    def test_host_edge_loss_still_rejected(self):
        def mutate(state):
            state['graph']['nodes'][0]['children'].pop()
        self.assertFalse(evaluate_with(mutate)['checks']['xref_graph_roles_and_statuses_match'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
