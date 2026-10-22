import linecache
import traceback
import unittest
from unittest.mock import Mock,patch

from manipulation_agent.executors.usd_diagnostics import MetadataTraceback


class USDCallerMetadataTests(unittest.TestCase):
    def test_edit_caller_metadata_does_not_read_source(self):
        proxy=MetadataTraceback(traceback)
        def editing_usd():return proxy.extract_stack(limit=3)
        with patch.object(linecache,'checkcache',side_effect=AssertionError('source I/O')),\
             patch.object(linecache,'getline',side_effect=AssertionError('source I/O')):
            frames=editing_usd()
            self.assertEqual(len(frames),3)
            self.assertEqual(frames[-1].name,'editing_usd')
            self.assertEqual(frames[-2].name,'test_edit_caller_metadata_does_not_read_source')
            self.assertEqual(frames[-1].filename,__file__)
            self.assertGreater(frames[-1].lineno,0)

    def test_other_tracebacks_retain_original_behavior(self):
        original=Mock();proxy=MetadataTraceback(original)
        self.assertIs(proxy.extract_stack(limit=8),original.extract_stack.return_value)
        self.assertEqual(original.extract_stack.call_args.kwargs,{'limit':8})
        self.assertIs(proxy.format_exc,original.format_exc)


if __name__=='__main__':unittest.main()
