"""Synthetic-only test of the helper's mkdir branch; never executes deployment.

On Linux, --gid 990 --reader-uid 994 can prove a real service identity reads only
this harness's synthetic 0600 file through the new 0750 parent. No real config
is opened. On Windows, POSIX mode assertions are explicitly NOT_RUN.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace


def main(args):
    source=args.helper.resolve()
    raw=source.read_bytes();tree=ast.parse(raw.decode('utf-8'))
    branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If)
                and ast.unparse(n.test)=='not config.parent.exists()')
    identity_function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='path_identity')
    namespace={'stat':stat}
    exec(compile(ast.Module(body=[identity_function],type_ignores=[]),'<identity-only>','exec'),namespace)
    identity=namespace['path_identity']
    posix=os.name=='posix'
    root_user=posix and os.geteuid()==0
    group=args.gid if args.gid is not None else (os.getgid() if posix else 0)
    if args.reader_uid is not None and not root_user:
        raise ValueError('ReaderCheckRequiresPOSIXRoot')
    parent=args.work_root.resolve()
    if not parent.is_dir() or parent.is_symlink():raise ValueError('InvalidWorkspaceParent')
    workspace=Path(tempfile.mkdtemp(prefix='signaldesk-health-parent-test-',dir=str(parent))).resolve()
    assert workspace.parent==parent and workspace.name.startswith('signaldesk-health-parent-test-')
    created_files=[];created_dirs=[]
    old_mask=os.umask(0o077)
    result={'passed':False,'helper_sha256':hashlib.sha256(raw).hexdigest(),'synthetic_only':True,
            'deployment_executed':False,'real_env_or_config_read':False,'posix_umask_mode_executed':posix,
            'reader_check_requested':args.reader_uid is not None,'checks':{}}
    try:
        if root_user:
            # Only this newly created synthetic workspace is made traversable
            # to the requested test reader; no existing directory is changed.
            os.chmod(workspace,0o750);os.chown(workspace,0,group)
        baseline=workspace/'baseline-mkdir-only'
        baseline.mkdir(mode=0o750);created_dirs.append(baseline)
        if root_user:os.chown(baseline,0,group)
        baseline_mode=stat.S_IMODE(baseline.stat().st_mode)
        if posix:assert baseline_mode==0o700,'RegressionTriggerNotObserved'
        result['checks']['mkdir750_under_umask077_is700']=True if posix else 'NOT_RUN_POSIX_REQUIRED'
        calls=[]
        def chown(path,uid,gid):
            mode=stat.S_IMODE(path.stat().st_mode)
            if posix:assert mode==0o750,'ChmodMustPrecedeChown'
            calls.append((uid,gid))
            if root_user:os.chown(path,uid,gid)
        def execute(directory):
            scope={'config':SimpleNamespace(parent=directory),'gid':group,'os':SimpleNamespace(chown=chown),
                   'path_identity':identity,'config_parent_identity':None}
            exec(compile(ast.Module(body=[branch],type_ignores=[]),'<new-parent-branch-only>','exec'),scope)
            return scope['config_parent_identity']
        new=workspace/'new-config-parent'
        captured=execute(new);created_dirs.append(new)
        assert captured==identity(new),'IdentityNotCaptured'
        assert calls==[(0,group)],'UnexpectedOwnershipCall'
        if posix:assert stat.S_IMODE(new.stat().st_mode)==0o750,'NewParentNot750'
        result['checks']['new_parent750_after_explicit_chmod']=True if posix else 'NOT_RUN_POSIX_REQUIRED'
        result['checks']['captured_identity_matches_created_parent']=True
        result['checks']['chmod_precedes_new_parent_chown']=True if posix else 'ORDER_ONLY_WINDOWS'
        for label,mode in [('existing-private',0o700),('existing-traversable',0o750)]:
            existing=workspace/label;existing.mkdir(mode=0o700);created_dirs.append(existing);existing.chmod(mode)
            if root_user:os.chown(existing,0,group)
            before=identity(existing);count=len(calls)
            assert execute(existing) is None,'ExistingParentClaimedCreated'
            assert identity(existing)==before and len(calls)==count,'ExistingParentModified'
        result['checks']['existing700_and750_parents_untouched']=True
        if args.reader_uid is not None:
            def reader(directory):
                target=directory/'synthetic-config-only'
                target.write_bytes(b'synthetic');target.chmod(0o600);os.chown(target,args.reader_uid,group)
                created_files.append(target)
                def drop_identity():
                    os.setgroups([]);os.setgid(group);os.setuid(args.reader_uid)
                code="from pathlib import Path;import sys;sys.exit(0 if Path(sys.argv[1]).read_bytes()==b'synthetic' else 2)"
                reply=subprocess.run([sys.executable,'-c',code,str(target)],stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,timeout=10,preexec_fn=drop_identity)
                return reply.returncode==0
            assert not reader(baseline),'Original700UnexpectedlyTraversable'
            assert reader(new),'Fixed750ReaderCannotReadSyntheticFile'
            result['checks']['reader_denied_by_baseline700']=True
            result['checks']['reader_can_read_own600_file_through_fixed750']=True
        result['new_parent_identity']=captured
        result['passed']=True
    except Exception as error:
        result['failure_type']=type(error).__name__
    finally:
        os.umask(old_mask)
        errors=[]
        # Exact known synthetic files and empty directories only; no recursion.
        for file in reversed(created_files):
            try:
                assert file.resolve().is_relative_to(workspace)
                file.unlink()
            except Exception as error:errors.append(type(error).__name__)
        for directory in reversed(created_dirs):
            try:
                assert directory.resolve().parent==workspace
                directory.rmdir()
            except Exception as error:errors.append(type(error).__name__)
        try:workspace.rmdir()
        except Exception as error:errors.append(type(error).__name__)
        result['synthetic_cleanup_errors']=errors
        if errors:result['passed']=False
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--work-root',type=Path,default=Path(tempfile.gettempdir()))
    parser.add_argument('--gid',type=int)
    parser.add_argument('--reader-uid',type=int)
    raise SystemExit(main(parser.parse_args()))
