// Evidence-only extraction through acadrust's existing public section reader.
// Does not assign bodies, repair objects, or write into the source directory.
use acadrust::DwgReader;
use std::{env, fs::OpenOptions, io::Write, path::Path};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 3 { return Err("usage: acds-dump source.dwg separate-output.bin".into()); }
    if Path::new(&args[1]).parent() == Path::new(&args[2]).parent() {
        return Err("use a separate evidence directory".into());
    }
    let mut reader = DwgReader::from_file(&args[1])?;
    let info = reader.read_file_header()?;
    let buffer = reader.get_section_buffer("AcDb:AcDsPrototype_1b", &info)?;
    let mut file = OpenOptions::new().write(true).create_new(true).open(&args[2])?;
    file.write_all(&buffer)?;
    println!("ACDS_BYTES {}", buffer.len());
    Ok(())
}
