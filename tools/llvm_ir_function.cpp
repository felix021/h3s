// Read one lazy LLVM bitcode function without materializing unrelated bodies.
// Some vendor modules contain malformed debug metadata in other functions.
// Output is a diagnostic IR fragment, not a standalone reassemblable module.
#include <llvm/Bitcode/BitcodeReader.h>
#include <llvm/IR/DebugInfo.h>
#include <llvm/IR/Function.h>
#include <llvm/IR/GVMaterializer.h>
#include <llvm/IR/LLVMContext.h>
#include <llvm/IR/Module.h>
#include <llvm/Support/Error.h>
#include <llvm/Support/MemoryBuffer.h>
#include <llvm/Support/raw_ostream.h>

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    llvm::LLVMContext context;
    auto buffer = llvm::MemoryBuffer::getFile(argv[1]);
    if (!buffer) { llvm::errs() << buffer.getError().message(); return 2; }
    auto module = llvm::getLazyBitcodeModule((*buffer)->getMemBufferRef(), context, true);
    if (!module) { llvm::logAllUnhandledErrors(module.takeError(), llvm::errs()); return 2; }
    (*module)->getMaterializer()->setStripDebugInfo();
    auto function = (*module)->getFunction(argv[2]);
    if (!function || function->isDeclaration()) return 3;
    if (auto error = function->materialize()) {
        llvm::logAllUnhandledErrors(std::move(error), llvm::errs()); return 2;
    }
    llvm::stripDebugInfo(*function);
    function->print(llvm::outs());
}
