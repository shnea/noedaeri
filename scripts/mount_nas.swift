// Credentials arrive only through stdin; NetFS UI is disabled for unattended use.
import Foundation
import NetFS

struct MountRequest: Decodable {
    let url: String
    let username: String?
    let password: String?
}

do {
    let request = try JSONDecoder().decode(MountRequest.self, from: FileHandle.standardInput.readDataToEndOfFile())
    guard let url = URL(string: request.url), url.scheme == "smb", url.password == nil else {exit(2)}
    let options = NSMutableDictionary(dictionary: ["UIOption": "NoUI", "NoUserPreferences": true])
    var mounts: Unmanaged<CFArray>?
    let result = NetFSMountURLSync(url as CFURL, nil, request.username as CFString?,
                                 request.password as CFString?, options, nil, &mounts)
    if let mounts { _ = mounts.takeRetainedValue() }
    print("{\"status\":\(result)}")
    exit(result == 0 ? 0 : 1)
} catch {
    print("{\"status\":-1}")
    exit(2)
}
