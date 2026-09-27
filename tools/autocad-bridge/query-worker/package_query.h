#pragma once

namespace ga::queryWorker {
// Only registered by the background worker, never by the user's GUI plugin.
void queryPackageCommand();
}
