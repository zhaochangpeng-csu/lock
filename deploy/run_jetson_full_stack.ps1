$ErrorActionPreference = "Stop"

$project = "/mnt/c/users/hoyo/desktop/lock"
$command = if ($args.Count -gt 0) { $args[0] } else { "run" }

wsl.exe bash -lc "cd '$project' && ./deploy/run_jetson_full_stack.sh '$command'"
if ($LASTEXITCODE -ne 0) {
    throw "Jetson full stack command failed with exit code $LASTEXITCODE"
}
