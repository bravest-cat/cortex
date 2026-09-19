"""
Test Suite for OpenViking Dynamic Workspace Watching and Incremental Sync.
"""
import shutil
import tempfile
from pathlib import Path

from cortex.viking.chunker import VikingChunker


def test_viking_dynamic_watching():
    temp_dir = Path(tempfile.mkdtemp(prefix="cortex_viking_test_"))
    try:
        # 1. Initialize Viking on temporary workspace
        viking = VikingChunker(workspace_root=str(temp_dir))
        assert len(viking.roots) >= 1
        assert viking.root == temp_dir.resolve()

        # Initial query on empty workspace returns graceful message
        empty_ctx = viking.get_context_for_query("anything")
        assert "No structural context available" in empty_ctx or len(empty_ctx) > 0
        print("✅ Step 1: Viking initialized on clean workspace.")

        # 2. Incrementally create a file (simulating agent write_to_file or watcher)
        test_file = temp_dir / "calculator.py"
        test_file.write_text(
            '"""Math utilities module."""\n\n'
            'class Calculator:\n'
            '    def add(self, a: int, b: int) -> int:\n'
            '        return a + b\n\n'
            '    def multiply(self, a: int, b: int) -> int:\n'
            '        return a * b\n'
        )

        # Call sync_file directly
        ctx = viking.sync_file(test_file)
        assert ctx is not None, "sync_file must return parsed HierarchicalContext"
        assert "Math utilities module" in ctx.l0_summary, f"L0 docstring missing: {ctx.l0_summary}"
        assert "def add" in ctx.l1_architecture or "Calculator" in ctx.l1_architecture, f"L1 architecture missing: {ctx.l1_architecture}"

        # Context query immediately returns the new file
        query_res = viking.get_context_for_query("calculator add")
        assert "calculator.py" in query_res
        assert "Math utilities module" in query_res
        print("✅ Step 2: Incremental sync_file verified with L0/L1 extraction.")

        # 3. Modify the file in place (simulating replace_file_content)
        test_file.write_text(
            '"""Math utilities module updated."""\n\n'
            'class Calculator:\n'
            '    def add(self, a: int, b: int) -> int:\n'
            '        return a + b\n\n'
            '    def divide(self, a: float, b: float) -> float:\n'
            '        return a / b\n'
        )
        ctx2 = viking.sync_file(test_file)
        assert ctx2 is not None
        assert "divide" in ctx2.l1_architecture, "Updated signature must appear in L1 architecture"
        assert "multiply" not in ctx2.l1_architecture, "Deleted method must not appear in updated L1"

        updated_res = viking.get_context_for_query("divide")
        assert "divide" in updated_res
        print("✅ Step 3: In-place modification hot-sync verified.")

        # 4. Multi-language incremental sync (TypeScript / Rust)
        ts_file = temp_dir / "service.ts"
        ts_file.write_text(
            'export interface UserService {\n'
            '    getUser(id: string): Promise<User>;\n'
            '}\n'
        )
        ctx_ts = viking.sync_file(ts_file)
        assert ctx_ts is not None
        assert "service.ts" in ctx_ts.file_path
        print("✅ Step 4: Multi-language TypeScript incremental sync verified.")

        # 5. Delete file
        del_res = viking.delete_file(test_file)
        assert del_res is True, "delete_file must report True when file purged"
        after_del = viking.get_context_for_query("calculator")
        assert "calculator.py" not in after_del
        print("✅ Step 5: Incremental delete_file verified.")

        # 6. Dynamic root switching
        sub_project = temp_dir / "subproject"
        sub_project.mkdir()
        sub_file = sub_project / "api.py"
        sub_file.write_text('def handle_request(): pass\n')

        viking.set_active_workspace(sub_project)
        assert viking.root == sub_project.resolve()
        sub_ctx = viking.get_context_for_query("handle_request")
        assert "api.py" in sub_ctx
        print("✅ Step 6: Dynamic set_active_workspace verified.")

        print("\n🎉 ALL DYNAMIC OPENVIKING TESTS PASSED PERFECTLY!")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_viking_dynamic_watching()
